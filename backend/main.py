"""
SuperLiving Ad Generator — FastAPI Backend
AI logic     → ai_engine.py
FFmpeg logic → video_engine.py
Async jobs   → this file (threading-based job store)
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from google import genai
from google.genai import types

from .ai_agents import (
    auto_generate_character_image,
    build_director_prompts,
    parse_script_for_characters,
)
from .ai_engine import (
    RaiCelebrityError,
    RaiContentError,
    analyze_character_photo,
    build_character_sheet,
    build_clip_prompts,
    download_video,
    extract_generated_video,
    generate_clip_from_image,
    generate_clip_text_only,
    generate_clip_with_frame_context,
    get_clip_character_photo,
    rephrase_blocked_prompt,
    sanitize_prompt_for_veo,
)
from .api_models import (
    AgenticPipelineRequest,
    AgenticPipelineResponse,
    AnalyzeCharactersResponse,
    CharacterAnalysis,
    CharacterProfile,
    ClipPrompt,
    GeneratePromptsRequest,
    GeneratePromptsResponse,
    GenerateVideoRequest,
    GenerateVideoResponse,
    RegenerateClipsRequest,
    RegenerateClipsResponse,
    VerifyPromptsRequest,
    VerifyPromptsResponse,
    ClipVerification
)
from .video_engine import stitch_clips, concat_with_normalized_cta

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TMP = tempfile.gettempdir()


def _unique_video_path(tag: str) -> str:
    return os.path.join(
        TMP,
        f"video_{tag}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.mp4",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Async Job Store
# ─────────────────────────────────────────────────────────────────────────────

class JobState(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    job_id: str
    state: JobState = JobState.PENDING
    progress: int = 0          # 0–100
    current_clip: int = 0      # 1-based, which clip is currently rendering
    total_clips: int = 0
    message: str = "Queued…"
    video_url: Optional[str] = None
    clip_paths: List[str] = field(default_factory=list)
    error: Optional[str] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)


_jobs: Dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _new_job(total_clips: int) -> Job:
    jid = str(uuid.uuid4())
    job = Job(job_id=jid, total_clips=total_clips)
    with _jobs_lock:
        _jobs[jid] = job
    return job


def _get_job(job_id: str) -> Optional[Job]:
    with _jobs_lock:
        return _jobs.get(job_id)


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="SuperLiving Ad Generator API",
    version="2.0.0",
    description="AI-powered video ad generation using Gemini + Veo",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_api_key() -> str:
    key = os.getenv("GOOGLE_API_KEY", "")
    if not key:
        raise HTTPException(status_code=500, detail="GOOGLE_API_KEY not configured on server.")
    return key


def _get_clients() -> tuple:
    api_key = _get_api_key()
    gemini_client = genai.Client(api_key=api_key)
    video_client  = genai.Client(api_key=api_key, http_options={"api_version": "v1alpha"})
    return gemini_client, video_client


# ─────────────────────────────────────────────────────────────────────────────
# Core video-generation logic (shared by sync + async paths)
# ─────────────────────────────────────────────────────────────────────────────

def _run_generate_video_core(
    clips: List[Any],
    veo_model: str,
    aspect_ratio: str,
    num_clips: int,
    anchor_image_b64: str = "",
    existing_clip_paths: Optional[List[str]] = None,
    indices_to_regen: Optional[List[int]] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Dict[str, Any]:
    """
    Shared rendering loop used by both the sync endpoint (legacy) and the
    async background threads.

    - For full generation:  existing_clip_paths=None, indices_to_regen=None
    - For partial regen:    existing_clip_paths=list of current paths,
                            indices_to_regen=0-based list of clips to redo

    progress_callback(current_clip_1based, total_clips, message) is called
    before each clip render so the job store can report live progress.
    """
    gemini_client, video_client = _get_clients()
    api_key = _get_api_key()

    is_regen = indices_to_regen is not None and existing_clip_paths is not None
    clip_paths: List[str] = list(existing_clip_paths) if is_regen else []
    MAX_RETRIES = 3

    # Which clips are we actually rendering?
    if is_regen:
        render_targets = sorted(indices_to_regen)
    else:
        render_targets = list(range(len(clips)))

    for render_order, i in enumerate(render_targets):
        clip = clips[i]
        clip_label = f"clip {clip.clip if hasattr(clip, 'clip') else i+1}/{num_clips}"
        scene = clip.scene_summary if hasattr(clip, "scene_summary") else ""

        if progress_callback:
            progress_callback(
                render_order + 1,
                len(render_targets),
                f"Rendering {clip_label}: {scene[:60]}…",
            )

        logger.info(f"🎥 {clip_label}: {scene}")
        current_prompt = clip.prompt if hasattr(clip, "prompt") else clip.get("prompt", "")
        operation = None

        # Pre-sanitize
        try:
            sanitized = sanitize_prompt_for_veo(gemini_client, current_prompt, i + 1)
            if sanitized and len(sanitized) > 100:
                current_prompt = sanitized
        except Exception as san_err:
            logger.warning(f"⚠️ Sanitizer failed ({san_err}) — using original prompt")

        for attempt in range(1, MAX_RETRIES + 1):
            if attempt > 1:
                current_prompt = rephrase_blocked_prompt(gemini_client, current_prompt, attempt)

            try:
                if i == 0:
                    # Clip 1: reference photo I2V or text-only fallback
                    if anchor_image_b64:
                        logger.info("🖼️ Clip 1: I2V from anchor reference image")
                        img_bytes = base64.b64decode(anchor_image_b64)
                        operation = generate_clip_from_image(
                            video_client, veo_model, current_prompt,
                            aspect_ratio, i + 1, num_clips,
                            img_bytes, "image/jpeg",
                        )
                    else:
                        logger.info("📝 Clip 1: no anchor image — text-only generation")
                        operation = generate_clip_text_only(
                            video_client, veo_model, current_prompt,
                            aspect_ratio, i + 1, num_clips,
                        )
                else:
                    # Clips 2+: last-frame I2V
                    prev_path = clip_paths[i - 1]
                    next_summary = (
                        clips[i].scene_summary
                        if hasattr(clips[i], "scene_summary")
                        else clips[i].get("scene_summary", "")
                    ) if i < len(clips) else ""
                    operation, current_prompt = generate_clip_with_frame_context(
                        video_client, gemini_client,
                        veo_model, current_prompt, aspect_ratio,
                        i + 1, num_clips,
                        prev_path, next_summary,
                    )

            except Exception as gen_err:
                err_str = str(gen_err)
                RETRYABLE = (
                    "503", "Deadline", "Broken pipe", "Errno 32",
                    "ConnectionReset", "RemoteDisconnected", "Connection reset",
                    "timed out", "timeout",
                )
                if any(e in err_str for e in RETRYABLE) and attempt < MAX_RETRIES:
                    wait = 15 * attempt
                    logger.warning(
                        f"⚠️ {clip_label} transient error (attempt {attempt}): "
                        f"{err_str[:120]} — sleeping {wait}s and retrying…"
                    )
                    time.sleep(wait)
                    continue
                # Non-retryable — fall back to text-only
                logger.warning(
                    f"⚠️ {clip_label} failed (attempt {attempt}): "
                    f"{err_str[:120]} — falling back to text-only"
                )
                operation = generate_clip_text_only(
                    video_client, veo_model, current_prompt,
                    aspect_ratio, i + 1, num_clips,
                )

            if operation is None:
                if attempt < MAX_RETRIES:
                    continue
                raise RuntimeError(f"{clip_label} timed out after {MAX_RETRIES} attempts.")

            try:
                video_obj = extract_generated_video(operation, i + 1)
            except RaiCelebrityError:
                logger.warning(f"🚫 {clip_label}: RAI celebrity — retrying text-only")
                operation = generate_clip_text_only(
                    video_client, veo_model, current_prompt,
                    aspect_ratio, i + 1, num_clips,
                )
                video_obj = None
                if operation:
                    try:
                        video_obj = extract_generated_video(operation, i + 1)
                    except (RaiCelebrityError, RaiContentError):
                        video_obj = None
            except RaiContentError:
                video_obj = None

            if video_obj is not None:
                break

            if attempt == MAX_RETRIES:
                raise RuntimeError(
                    f"{clip_label} blocked after {MAX_RETRIES} attempts. "
                    "Try editing the prompt and regenerating."
                )

        # Save clip
        if is_regen:
            clip_path = _unique_video_path(f"clip_{i+1:02d}_regen")
        else:
            clip_path = _unique_video_path(f"clip_{i+1:02d}")

        video_bytes = download_video(video_obj.uri, api_key)
        with open(clip_path, "wb") as f:
            f.write(video_bytes)

        if is_regen:
            clip_paths[i] = clip_path
        else:
            clip_paths.append(clip_path)

        logger.info(f"✅ {clip_label} saved ({len(video_bytes) // 1024} KB)")

    # ── Stitch ────────────────────────────────────────────────────────────────
    if progress_callback:
        progress_callback(len(render_targets), len(render_targets), "Stitching clips…")

    tag = "regen_final" if is_regen else "final"
    final_path = _unique_video_path(tag)
    if len(clip_paths) > 1:
        ok = stitch_clips(clip_paths, final_path)
        if not ok:
            final_path = clip_paths[0]
    else:
        final_path = clip_paths[0]

    # ── Append CTA ────────────────────────────────────────────────────────────
    cta_tag = "regen_with_cta" if is_regen else "final_with_cta"
    cta_appended_path = _unique_video_path(cta_tag)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(base_dir)
    cta_video_path = os.path.join(project_root, "assets", "cta.mp4")

    if os.path.exists(cta_video_path):
        cta_success = concat_with_normalized_cta(final_path, cta_video_path, cta_appended_path, aspect_ratio=aspect_ratio)
        if cta_success:
            final_path = cta_appended_path
        else:
            logger.warning("⚠️ Failed to append CTA.")
    else:
        logger.warning(f"⚠️ CTA video not found at: {cta_video_path}")

    return {
        "video_url": f"/api/video/{os.path.basename(final_path)}",
        "clip_paths": clip_paths,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Background thread workers (for async endpoints)
# ─────────────────────────────────────────────────────────────────────────────

def _thread_generate_video(job: Job, request_data: dict):
    """Background thread: full video generation."""
    try:
        job.state   = JobState.RUNNING
        job.message = "Initializing Veo clients…"
        job.progress = 2

        def _cb(clip_num: int, total: int, message: str):
            if job.cancel_event.is_set():
                raise RuntimeError("Job cancelled by user")
            job.current_clip = clip_num
            job.total_clips  = total
            # Reserve last 5% for stitching
            job.progress = max(5, int((clip_num - 1) / max(total, 1) * 90))
            job.message  = message
            logger.info(f"[job {job.job_id[:8]}] {message}")

        # Convert plain dicts → ClipPrompt-like objects
        clips = [_DictObj(c) for c in request_data["clips"]]

        result = _run_generate_video_core(
            clips=clips,
            veo_model=request_data["veo_model"],
            aspect_ratio=request_data["aspect_ratio"],
            num_clips=request_data["num_clips"],
            anchor_image_b64=request_data.get("anchor_image_b64", ""),
            progress_callback=_cb,
        )

        job.video_url  = result["video_url"]
        job.clip_paths = result["clip_paths"]
        job.progress   = 100
        job.message    = "Done! Your ad is ready. 🎉"
        job.state      = JobState.DONE
        logger.info(f"✅ Job {job.job_id} done: {result['video_url']}")

    except Exception as exc:
        if job.cancel_event.is_set():
            job.state   = JobState.CANCELLED
            job.message = "Cancelled by user."
        else:
            job.state   = JobState.FAILED
            job.error   = str(exc)
            job.message = f"Failed: {exc}"
            logger.exception(f"❌ Job {job.job_id} failed")


def _thread_regenerate_clips(job: Job, request_data: dict):
    """Background thread: partial clip regeneration."""
    try:
        job.state   = JobState.RUNNING
        job.message = "Starting clip regeneration…"
        job.progress = 2

        def _cb(clip_num: int, total: int, message: str):
            if job.cancel_event.is_set():
                raise RuntimeError("Job cancelled by user")
            job.current_clip = clip_num
            job.total_clips  = total
            job.progress = max(5, int((clip_num - 1) / max(total, 1) * 90))
            job.message  = message

        clips = [_DictObj(c) for c in request_data["clips"]]

        result = _run_generate_video_core(
            clips=clips,
            veo_model=request_data["veo_model"],
            aspect_ratio=request_data["aspect_ratio"],
            num_clips=request_data["num_clips"],
            existing_clip_paths=request_data["clip_paths"],
            indices_to_regen=request_data["clip_indices"],
            progress_callback=_cb,
        )

        job.video_url  = result["video_url"]
        job.clip_paths = result["clip_paths"]
        job.progress   = 100
        job.message    = "Done! Clips regenerated. 🎉"
        job.state      = JobState.DONE

    except Exception as exc:
        if job.cancel_event.is_set():
            job.state   = JobState.CANCELLED
            job.message = "Cancelled by user."
        else:
            job.state   = JobState.FAILED
            job.error   = str(exc)
            job.message = f"Failed: {exc}"
            logger.exception(f"❌ Regen job {job.job_id} failed")


class _DictObj:
    """Wraps a dict so attribute access works the same as a Pydantic model."""
    def __init__(self, d: dict):
        self.__dict__.update(d)

    def get(self, key, default=None):
        return self.__dict__.get(key, default)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


# ── Job status polling ────────────────────────────────────────────────────────

@app.get("/api/job-status/{job_id}")
async def get_job_status(job_id: str):
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return {
        "job_id":       job.job_id,
        "status":       job.state.value,
        "progress":     job.progress,
        "current_clip": job.current_clip,
        "total_clips":  job.total_clips,
        "message":      job.message,
        "video_url":    job.video_url,
        "clip_paths":   job.clip_paths if job.clip_paths else None,
        "error":        job.error,
    }


@app.get("/api/cancel-job/{job_id}")
async def cancel_job(job_id: str):
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    job.cancel_event.set()
    job.state   = JobState.CANCELLED
    job.message = "Cancelled by user."
    return {"cancelled": True}


# ── Async video generation (used by the new frontend) ────────────────────────

@app.post("/api/generate-video-async")
async def generate_video_async(request: GenerateVideoRequest):
    """
    Starts video generation in a background thread.
    Returns {job_id} immediately. Frontend polls /api/job-status/{job_id}.
    """
    job = _new_job(total_clips=request.num_clips)
    request_data = {
        "clips":            [c.dict() for c in request.clips],
        "veo_model":        request.veo_model,
        "aspect_ratio":     request.aspect_ratio,
        "num_clips":        request.num_clips,
        "anchor_image_b64": "",  # photo references handled via anchor_image_b64 field if needed
    }
    t = threading.Thread(
        target=_thread_generate_video,
        args=(job, request_data),
        daemon=True,
        name=f"veo-{job.job_id[:8]}",
    )
    t.start()
    logger.info(f"🚀 Async job {job.job_id} started (thread {t.name})")
    return {"job_id": job.job_id}


@app.post("/api/regenerate-clips-async")
async def regenerate_clips_async(request: RegenerateClipsRequest):
    """
    Starts clip regeneration in a background thread.
    Returns {job_id} immediately.
    """
    job = _new_job(total_clips=len(request.clip_indices))
    request_data = {
        "clips":        [c.dict() for c in request.clips],
        "clip_paths":   list(request.clip_paths),
        "clip_indices": list(request.clip_indices),
        "veo_model":    request.veo_model,
        "aspect_ratio": request.aspect_ratio,
        "num_clips":    request.num_clips,
    }
    t = threading.Thread(
        target=_thread_regenerate_clips,
        args=(job, request_data),
        daemon=True,
        name=f"veo-regen-{job.job_id[:8]}",
    )
    t.start()
    logger.info(f"🚀 Async regen job {job.job_id} started — clips {request.clip_indices}")
    return {"job_id": job.job_id}


# ── Agentic pipeline ──────────────────────────────────────────────────────────

@app.post("/api/agentic-pipeline", response_model=AgenticPipelineResponse)
async def agentic_pipeline(request: AgenticPipelineRequest):
    """
    Orchestrates Phases 1-3 of the SuperLiving Auto-Director pipeline:
      Phase 1 — Parser Agent: Gemini extracts characters from the script.
      Phase 2 — Imagen Agent: generates 9:16 reference faces for each character.
      Phase 3 — Director Agent: Gemini splits the script into Veo 3.1 clip prompts.
    Returns characters (with reference images) + clip prompts for Phase 4 (Human Review).
    """
    gemini_client, _ = _get_clients()
    api_key = _get_api_key()

    logger.info("🎬 Phase 1 — Parsing characters from script…")
    try:
        characters_json = parse_script_for_characters(gemini_client, request.script)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Phase 1 (Parser Agent) failed: {e}")

    logger.info("🖼️ Phase 2 — Generating reference images via Imagen…")
    character_profiles: list[CharacterProfile] = []
    for char in characters_json.get("characters", []):
        ref_image_b64 = ""
        try:
            ref_image_b64 = auto_generate_character_image(
                api_key,
                char.get("physical_baseline", ""),
                char.get("outfit", ""),
            )
        except Exception as e:
            logger.warning(f"⚠️ Imagen failed for {char.get('name', '?')}: {e}")

        character_profiles.append(CharacterProfile(
            id=char.get("id", ""),
            name=char.get("name", ""),
            physical_baseline=char.get("physical_baseline", ""),
            outfit=char.get("outfit", ""),
            reference_image_base64=ref_image_b64,
        ))

    logger.info("🎥 Phase 3 — Building director prompts…")
    try:
        clips = build_director_prompts(
            gemini_client, request.script, characters_json, request.num_clips,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Phase 3 (Director Agent) failed: {e}")

    return AgenticPipelineResponse(
        characters=character_profiles,
        clips=[ClipPrompt(**c) for c in clips],
        message=(
            f"Pipeline complete — {len(character_profiles)} character(s) extracted, "
            f"{len(clips)} clip prompt(s) generated. Ready for Phase 4 (Human Review)."
        ),
    )


# ── Analyze character photos ──────────────────────────────────────────────────

@app.post("/api/analyze-characters", response_model=AnalyzeCharactersResponse)
async def analyze_characters(
    names: list[str] = Form(...),
    photos: list[UploadFile] = File(...),
):
    """
    Accepts uploaded images + character names.
    Runs analyze_character_photo for each, returns locked JSON appearance/outfit.
    """
    if len(names) != len(photos):
        raise HTTPException(
            status_code=400,
            detail="Number of names must match number of photos.",
        )

    gemini_client, _ = _get_clients()
    analyses: dict[str, CharacterAnalysis] = {}

    for name, photo in zip(names, photos):
        name = name.strip()
        if not name:
            continue
        photo_bytes = await photo.read()
        mime_type = photo.content_type or "image/jpeg"
        try:
            result = analyze_character_photo(gemini_client, name, photo_bytes, mime_type)
            analyses[name] = CharacterAnalysis(**result)
        except Exception as e:
            logger.warning(f"Could not analyse {name}: {e}")
            analyses[name] = CharacterAnalysis(appearance="", outfit="")

    return AnalyzeCharactersResponse(analyses=analyses)


# ── Generate prompts ──────────────────────────────────────────────────────────

@app.post("/api/generate-prompts", response_model=GeneratePromptsResponse)
async def generate_prompts(request: GeneratePromptsRequest):
    """
    Accepts the script, character data, and settings.
    Runs build_clip_prompts and returns the array of prompts for user review.
    """
    gemini_client, _ = _get_clients()

    character_sheet = request.character_sheet
    if not request.has_photos and not character_sheet:
        try:
            character_sheet = build_character_sheet(gemini_client, request.script)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Character sheet generation failed: {e}")

    photo_analyses_dict = {
        name: {"appearance": data.appearance, "outfit": data.outfit}
        for name, data in request.photo_analyses.items()
    }

    try:
        clips = build_clip_prompts(
            client=gemini_client,
            script=request.script,
            extra_prompt=request.extra_prompt,
            extra_image_parts=[],
            character_sheet=character_sheet,
            photo_analyses=photo_analyses_dict,
            aspect_ratio=request.aspect_ratio,
            num_clips=request.num_clips,
            language_note=request.language_note,
            has_photos=request.has_photos,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prompt generation failed: {e}")

    return GeneratePromptsResponse(
        clips=[ClipPrompt(**c) for c in clips],
        character_sheet=character_sheet,
    )


# ── Sync generate-video (legacy — kept for backward compat) ──────────────────

@app.post("/api/generate-video", response_model=GenerateVideoResponse)
async def generate_video(request: GenerateVideoRequest):
    """
    Synchronous video generation — DEPRECATED in favour of /api/generate-video-async.
    Kept here so any existing callers don't break. New frontend uses the async endpoint.
    NOTE: This will timeout on large jobs (>2 min) for most HTTP clients.
    """
    anchor_image_b64 = ""
    if hasattr(request, "characters") and request.characters:
        for char in request.characters:
            if getattr(char, "reference_image_base64", ""):
                anchor_image_b64 = char.reference_image_base64
                logger.info(f"🖼️ Clip 1 anchor image found for '{char.name}'")
                break

    try:
        result = _run_generate_video_core(
            clips=request.clips,
            veo_model=request.veo_model,
            aspect_ratio=request.aspect_ratio,
            num_clips=request.num_clips,
            anchor_image_b64=anchor_image_b64,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return GenerateVideoResponse(
        video_url=result["video_url"],
        clip_paths=result["clip_paths"],
        message=f"Successfully generated {request.num_clips} clip(s).",
    )


# ── Sync regenerate-clips (legacy) ───────────────────────────────────────────

@app.post("/api/regenerate-clips", response_model=RegenerateClipsResponse)
async def regenerate_clips(request: RegenerateClipsRequest):
    """
    Synchronous clip regeneration — DEPRECATED in favour of /api/regenerate-clips-async.
    """
    try:
        result = _run_generate_video_core(
            clips=request.clips,
            veo_model=request.veo_model,
            aspect_ratio=request.aspect_ratio,
            num_clips=request.num_clips,
            existing_clip_paths=list(request.clip_paths),
            indices_to_regen=list(request.clip_indices),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return RegenerateClipsResponse(
        video_url=result["video_url"],
        clip_paths=result["clip_paths"],
        message=f"Successfully regenerated {len(request.clip_indices)} clip(s).",
    )


# ── Serve generated videos ────────────────────────────────────────────────────

@app.get("/api/video/{filename}")
async def serve_video(filename: str):
    """Serve a generated video file from the temp directory."""
    path = os.path.join(TMP, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Video file not found.")
    return FileResponse(path, media_type="video/mp4", filename=filename)




GEMINI_PROMPT_AUDITOR = """You are a ruthless AI video prompt auditor for SuperLiving — an Indian health & wellness app.
 
Your single job: Make every Veo 3.1 prompt generate a REALISTIC, hallucination-free, cinematic video.
You audit by the rules below. When something is wrong — fix it. Return the corrected prompt.
 
A bad prompt = ghost face, drifting character, background objects appearing/disappearing,
broken lip-sync, or a video that looks obviously AI-generated and fake.
 
════════════════════════════════════════════════════════════
RULE 1 — DIALOGUE WORD COUNT (15–19 HINDI WORDS EXACTLY)
════════════════════════════════════════════════════════════
Count every word in the dialogue. Include quoted words inside the dialogue.
 
Under 15 → slow-motion speech, awkward silence between words
Over 19 → chipmunk rush, lip movements don't match
Exactly 15–19 → perfect 8-second sync
 
FIX: Trim or expand. Keep the emotional core. Do not change speaker or tone.
Count again after fixing — confirm 15–19.
 
════════════════════════════════════════════════════════════
RULE 2 — SINGLE ACTION ONLY (ZERO TOLERANCE FOR TRANSITIONS)
════════════════════════════════════════════════════════════
Each clip = one static state. NOT a sequence. NOT a transition.
 
FLAG and FIX any of these patterns in ACTION block:
✗ "expression changes from X to Y" → show only the FINAL state
✗ "looks down at phone, then back at camera" → remove the look-down
✗ "slowly smiles / gradually becomes confident" → just "चेहरे पर मुस्कान है"
✗ "raises hand into frame" → hands must be visible from clip start or out of frame entirely
✗ Multiple verbs: "takes a breath, looks up, and smiles" → pick ONE
✗ "eyes light up as he realizes" → transition language → remove
 
CORRECT ACTION format:
(STATIC SHOT) चेहरे पर [ONE EXPRESSION]. शरीर बिल्कुल स्थिर रहता है, हाथ नीचे ही रहेंगे।
 
════════════════════════════════════════════════════════════
RULE 3 — LIGHTING: GHOST FACE PREVENTION
════════════════════════════════════════════════════════════
INSTANT FLAG: Any clip where a SINGLE overhead OR bottom-up source is the ONLY light.
 
Top-down only → black eye sockets, skull shadows, horror face
Bottom-up only (phone screen) → chin bright, eyes dark, ghost effect
No fill → character looks like a nightmare even with "cinematic contrast"
 
MANDATORY FIX — every clip needs DUAL sources:
  PRIMARY: Soft warm side-fill from LEFT or RIGHT (table lamp, window, ambient)
           → fills eye sockets, makes face human
  SECONDARY: Overhead or background ambient (very low intensity)
 
Example of correct lighting block:
"प्रकाश: दाईं ओर से एक डिम, गर्म warm-white साइड-फिल लाइट — आँखें और माथा clearly
रोशन हैं। ऊपर से हल्की ambient रोशनी।
⚠️ आँखें clearly visible। कोई काले eye socket shadows नहीं।
Cinematic contrast, photorealistic skin texture, extremely crisp."
 
════════════════════════════════════════════════════════════
RULE 4 — NO VOICEOVER (ZERO TOLERANCE)
════════════════════════════════════════════════════════════
INSTANT FLAG: Any dialogue line assigned to a character NOT visible on screen.
 
Keywords to catch: वॉयसओवर, voiceover, off-screen, ऑफ-स्क्रीन, (VO), voice over,
"ऋषिका (ऑफ-स्क्रीन):", "Rishika (voiceover):"
 
WHY: Veo syncs lip movements to the on-screen character. An off-screen speaker has
no face to sync to — result is silence, random mouth movement, or a hallucinated face.
 
FIX: Convert to on-screen character quoting the off-screen person:
BEFORE: ऋषिका (वॉयसओवर): "यार, चिल कर।"
AFTER:  राहुल: "(बातचीत के लहजे में) ऋषिका ने कहा — 'यार, चिल कर।'"
 
════════════════════════════════════════════════════════════
RULE 5 — PHONE SCREEN TRAP
════════════════════════════════════════════════════════════
If any character holds or views a phone:
 
MUST have: "फोन की स्क्रीन काली है — कोई UI, text, app, chat या face नहीं।"
NEVER describe: message bubbles, app interface, profile photo, WhatsApp, Instagram UI
NEVER: second character's face shown inside the phone screen
NEVER: instructions like "phone shows a notification" or "he scrolls his feed"
 
Veo will hallucinate a face/UI if not explicitly blocked.
 
════════════════════════════════════════════════════════════
RULE 6 — BACKGROUND LOCK (FATAL IF VIOLATED)
════════════════════════════════════════════════════════════
Every clip's LOCATION block must be VERBATIM identical to clip 1.
The freeze line must appear at the end:
"पृष्ठभूमि पूरी तरह स्थिर और अपरिवर्तित रहती है — कोई नई वस्तु नहीं आएगी,
कोई वस्तु गायब नहीं होगी, रंग नहीं बदलेगा।"
 
FLAG: If any clip's LOCATION differs from clip 1 (even slightly paraphrased).
FLAG: If CONTINUING FROM mentions a DIFFERENT location than the LOCKED BACKGROUND.
FIX: Replace with verbatim clip 1 LOCATION.
 
════════════════════════════════════════════════════════════
RULE 7 — CONTINUING FROM AND LAST FRAME
════════════════════════════════════════════════════════════
Every clip except clip 1 MUST have a CONTINUING FROM block.
Every clip MUST have a LAST FRAME block.
 
CONTINUING FROM must include:
  - Character: exact expression, exact hand position, body position
  - Background: full object inventory (every item, every shelf, positions)
  - Camera: shot type
  - Lighting: direction and color temperature
 
LAST FRAME must use identical format — it becomes the next clip's CONTINUING FROM.
 
FLAG: Missing CONTINUING FROM (clips 2+)
FLAG: Missing LAST FRAME (any clip)
FLAG: CONTINUING FROM says "previous character not here" without explaining the new scene
FIX for new scene: "यह एक नया, स्वतंत्र दृश्य है। पिछले क्लिप के चरित्र और
पृष्ठभूमि यहाँ नहीं हैं।" + full new scene description
 
════════════════════════════════════════════════════════════
RULE 8 — FACE LOCK INTEGRITY
════════════════════════════════════════════════════════════
Every clip must have: ⚠️ चेहरा पूरी तरह स्थिर और क्लिप 1 के समान रहेगा...
 
CRITICAL: If a clip features a DIFFERENT CHARACTER than clip 1 —
the Face Lock MUST reference that character's own face, NOT "same as clip 1".
 
FLAG: Clip 4 shows Rishika but Face Lock says "same as clip 1" (where clip 1 shows a man)
FIX: Write a new Face Lock for the new character referencing only their appearance.
 
════════════════════════════════════════════════════════════
RULE 9 — REALISM CHECKS (WHAT MAKES IT LOOK REAL)
════════════════════════════════════════════════════════════
FLAG any of these realism-breaking patterns and fix:
 
a) OVER-THEATRICAL EXPRESSIONS
   ✗ "चौड़ी, बड़ी, खुश मुस्कान" → ✓ "हल्की, सच्ची मुस्कान"
   ✗ "आँखें चमक उठती हैं" → ✓ "आँखों में हल्की चमक है"
   Real humans show subtle micro-expressions. Big theatrical expressions = AI-looking.
 
b) LIGHTING DESCRIPTION CONTRADICTIONS
   ✗ "tubelight is now less harsh because of his confidence"
   Light does not change based on emotion. Remove emotional qualifiers from lighting.
   ✓ Keep: fixed light source description. Remove: subjective feel language.
 
c) DOUBLE COLON IN SECTION HEADERS
   ✗ CONTINUING FROM:: → ✓ CONTINUING FROM:
 
d) SKIN TEXTURE MISSING
   Every LIGHTING block should include: "photorealistic skin texture" or
   "extremely crisp" — this forces Veo to render real pores and natural skin.
 
e) OUTFIT BLOCK MISSING PHYSICAL APPEARANCE
   OUTFIT & APPEARANCE must contain BOTH outfit AND physical description.
   If only outfit is listed — flag and request full appearance block.
 
f) BACKGROUND IS NOT IN FOCUS
   "पृष्ठभूमि पूरी तरह से फोकस में है" — this is a mistake. Background should be
   SLIGHTLY out of focus to separate character from environment (natural depth of field).
   FIX: Remove "पूरी तरह से फोकस में" or replace with "हल्की natural depth of field"
 
g) CAMERA MOVEMENT
   Any pan, zoom, tilt, track = removes UGC/realistic feel.
   ✗ "camera slowly zooms in" → ✓ (STATIC SHOT), कैमरा बिल्कुल स्थिर
 
════════════════════════════════════════════════════════════
RULE 10 — FORMAT PROHIBITIONS PRESENT
════════════════════════════════════════════════════════════
Every clip must contain:
"No cinematic letterbox bars. No black bars. Full 9:16 vertical portrait frame edge to edge.
No burned-in subtitles. No text overlays. No lower thirds. No captions. No watermarks.
No on-screen app UI. If showing phone, show dark screen only.
Audio-visual sync: match lip movements precisely to spoken dialogue."
 
FLAG if missing. ADD if not present.
 
════════════════════════════════════════════════════════════
RULE 11 — EMOTIONAL AUTHENTICITY (AD EFFECTIVENESS)
════════════════════════════════════════════════════════════
This is a SuperLiving ad for Tier 3/4 India users aged 18–35.
The ad must make the viewer feel: recognition, relief, hope, belonging.
 
FLAG if:
- Dialogue sounds scripted or formal ("मैं सुपरलिविंग एप्लिकेशन का उपयोग करता हूँ")
- Dialogue has motivational-poster language ("विश्वास करो, सब ठीक होगा")
- Clip 1 hook does not establish an immediately relatable specific problem
- Rishika's lines sound like a coach, not a friend
 
The best dialogue sounds like someone telling their friend exactly what happened.
VERBATIM real user phrases are better than polished scripted lines.
 
════════════════════════════════════════════════════════════
OUTPUT FORMAT — valid JSON only, no markdown, no explanation
════════════════════════════════════════════════════════════
{
  "clips": [
    {
      "clip": 1,
      "status": "approved" or "improved",
      "issues": [
        "Specific issue description — what was wrong and where",
        "Another issue"
      ],
      "improved_prompt": "Full corrected Hindi prompt. Identical to input if approved."
    }
  ],
  "overall_score": 78,
  "summary": "One sentence: what the main problems were and what was fixed."
}
 
Rules for issues list:
- Empty array [] if status is "approved"
- Be specific: not "lighting problem" but "bottom-up phone screen as only light source
  will cause ghost face — added warm side-fill from right as primary, phone glow as secondary accent"
- Not "word count issue" but "Clip 2 dialogue is 23 words — trimmed to 18 by removing
  'और मुझे बहुत बुरा लगा' which was redundant"
 
Rules for improved_prompt:
- Must be the COMPLETE prompt with ALL sections, not just the changed parts
- If status is "approved" — improved_prompt MUST equal the original prompt exactly
- Write in Devanagari Hindi (same as input)"""
 
@app.post("/api/verify-prompts", response_model=VerifyPromptsResponse)
async def verify_prompts(request: VerifyPromptsRequest):
    """
    Phase 4.5 — Gemini Verification Agent.
    Sends all clip prompts to Gemini for emotional enrichment audit.
    Returns per-clip status, issues found, and improved prompts if needed.
    """
    import json

    gemini_client, _ = _get_clients()

    # Build the user message with all clips
    clips_text = ""
    for clip in request.clips:
        clips_text += f"\n\n{'='*60}\nCLIP {clip.clip} — {clip.scene_summary}\n{'='*60}\n{clip.prompt}"

    user_message = f"""Please audit these {len(request.clips)} clip prompts for a SuperLiving ad video.

ORIGINAL SCRIPT CONTEXT:
{request.script if request.script else "Not provided"}

CLIP PROMPTS TO AUDIT:
{clips_text}

Check every rule strictly. Return JSON only."""

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=GEMINI_PROMPT_AUDITOR,
                response_mime_type="application/json",
                temperature=0.3,
                max_output_tokens=65536,
            ),
        )

        raw = response.text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)

        return VerifyPromptsResponse(
            clips=[ClipVerification(**c) for c in data["clips"]],
            overall_score=data.get("overall_score", 0),
            summary=data.get("summary", ""),
        )

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Gemini returned invalid JSON: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Verification failed: {e}")
 
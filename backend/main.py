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
        cta_success = concat_with_normalized_cta(final_path, cta_video_path, cta_appended_path)
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

CLAUDE_VERIFY_SYSTEM_PROMPT = """You are a STRICT AI Video Ad Prompt Auditor for SuperLiving — an Indian health & wellness app targeting Tier 3/4 India users aged 18-35.
 
Your job: Review each Veo video generation prompt and fix ANY issues. Be ruthless. A bad prompt wastes money and generates ghost-faced horror videos.
 
═══════════════════════════════════════════════════════
RULES YOU MUST ENFORCE (reject or fix anything that violates these)
═══════════════════════════════════════════════════════
 
1. WORD COUNT — GOLDILOCKS ZONE
   - DIALOGUE must be 15–19 Hindi/Hinglish words. Count every word.
   - Under 15 = slow-motion speech. Over 19 = chipmunk rush, lip-sync breaks.
   - Fix: trim or expand dialogue to hit 15–19 words exactly.
 
2. SINGLE ACTION ONLY
   - ACTION block must describe ONE emotional state OR one physical state.
   - NEVER: "expression shifts from sad to happy AND he looks down at his phone"
   - NEVER: multiple verbs in ACTION block (looks down THEN looks back THEN smiles)
   - Fix: split across clips or keep only the dominant action.
 
3. LIGHTING — NO HORROR, NO GHOST
   - NEVER: bottom-up phone light as the ONLY light source.
   - This creates black eye sockets and ghost/horror faces.
   - Fix: always add a dim warm ambient source (bedside lamp, window glow) from the SIDE or ABOVE. Phone can be secondary accent only.
   - Eye sockets MUST be visible. Add: "⚠️ आँखें clearly visible — कोई काले eye socket shadows नहीं"
 
4. PHONE SCREEN TRAP
   - If phone is shown: screen MUST be black. Add "फोन की स्क्रीन काली है — कोई UI, app, text नहीं।"
   - NEVER describe a chat interface, app UI, profile picture, or text on screen.
 
5. FACE/CHARACTER LOCK
   - Every clip (except clip 1) MUST have a CONTINUING FROM block with exact last-frame description.
   - CONTINUING FROM must include: character expression, hand position, full background object inventory.
 
6. BACKGROUND LOCK — FATAL IF VIOLATED
   - LOCATION block must be IDENTICAL in every clip (copy-paste verbatim from clip 1).
   - Must end with: "पृष्ठभूमि पूरी तरह स्थिर और अपरिवर्तित रहती है — कोई नई वस्तु नहीं आएगी, कोई वस्तु गायब नहीं होगी, रंग नहीं बदलेगा।"
   - If CONTINUING FROM mentions a DIFFERENT location than the LOCKED BACKGROUND (e.g., "brick wall / cafe" vs "bedroom") — this is a FATAL error. Fix the CONTINUING FROM to match the locked background.
 
7. NO MULTIPLE CHARACTERS IN FRAME
   - Only ONE character should be visible on screen at any time.
   - A second character's FACE must NEVER appear in the frame.
   - Off-screen sounds (laughter, voice) are allowed ONLY in the AUDIO block.
 
8. NO VOICEOVER — ZERO TOLERANCE
   - NEVER assign dialogue to a character who is NOT physically on screen.
   - Keywords to flag: "वॉयसओवर", "voiceover", "off-screen", "ऑफ-स्क्रीन", "(VO)", "voice over"
   - Veo has no face to sync voiceover to — it generates broken lip movements or silence.
   - Fix: Convert all voiceover lines into the ON-SCREEN character's narrated speech.
     BAD:  "ऋषिका (वॉयसओवर): 'यार चिल कर...'"
     GOOD: "राहुल: '(बातचीत के लहजे में) रिशिका ने बोला — यार चिल कर...'"
   - The on-screen character quotes, remembers, or paraphrases what the off-screen character said.
 
9. EMOTIONAL AUTHENTICITY — THE AD MUST CONNECT
   - The ad must make the viewer FEEL something: recognition, relief, hope, belonging.
   - Dialogue must sound like a REAL person talking to a friend — not a script being read.
   - NO LinkedIn-poster language. NO motivational clichés.
   - The protagonist's pain must be VERBATIM real — use exact phrases Indian users say.
   - Clip 1 hook must grab in 3 seconds. If not, flag it.
 
10. FORMAT PROHIBITIONS — every clip must state these:
    "No cinematic letterbox bars. No black bars. Full 9:16 vertical portrait frame edge to edge. No burned-in subtitles. No text overlays. No lower thirds. No captions. No watermarks. No on-screen app UI."
 
11. FACE LOCK STATEMENT — must appear in every clip:
    "⚠️ चेहरा पूरी तरह स्थिर और क्लिप 1 के समान रहेगा — चेहरे की बनावट, त्वचा का रंग, आँखें, होंठ, बाल — कोई परिवर्तन नहीं।"
 
12. LAST FRAME — must appear in every clip with full background object inventory.
 
═══════════════════════════════════════════════════════
OUTPUT FORMAT — respond ONLY with valid JSON, no markdown
═══════════════════════════════════════════════════════
 
{
  "clips": [
    {
      "clip": 1,
      "status": "approved" or "improved",
      "issues": ["list of specific problems found, empty if approved"],
      "improved_prompt": "the full corrected prompt text — identical to input if approved"
    }
  ],
  "overall_score": 85,
  "summary": "One line: what was wrong overall and what was fixed"
}
 
Be specific in issues. Not "voiceover problem" — say "Clip 3 has Rishika voiceover — Veo cannot lip-sync off-screen character. Converted to Rahul quoting Rishika's line in his own dialogue."
If a prompt is already perfect: status = "approved", issues = [], improved_prompt = original.
"""
 
 
 
@app.post("/api/verify-prompts", response_model=VerifyPromptsResponse)
async def verify_prompts(request: VerifyPromptsRequest):
    """
    Phase 4.5 — Claude Verification Agent.
    Sends all clip prompts to Claude claude-sonnet-4-20250514 for strict audit.
    Returns per-clip status, issues found, and improved prompts if needed.
    """
    import anthropic
    import json
 
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured on server.")
 
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
        client = anthropic.Anthropic(api_key=anthropic_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=16000,
            system=CLAUDE_VERIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}]
        )
 
        raw = message.content[0].text.strip()
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
        raise HTTPException(status_code=500, detail=f"Claude returned invalid JSON: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Verification failed: {e}")
 
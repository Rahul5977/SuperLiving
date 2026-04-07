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
from .prompts import GEMINI_PROMPT_AUDITOR
from .ai_engine import (
    RaiCelebrityError,
    RaiContentError,
    analyse_script_for_production,
    analyze_character_photo,
    build_character_sheet,
    build_clip_prompts,
    download_video,
    extract_generated_video,
    generate_clip_from_image,
    generate_clip_text_only,
    generate_clip_with_frame_context,
    get_clip_character_photo,
    hyphenate_dialogue_acronyms,
    rephrase_blocked_prompt,
    sanitize_prompt_for_veo,
)
from .api_models import (
    AgenticPipelineRequest,
    AgenticPipelineResponse,
    AnalyseScriptRequest,
    AnalyseScriptResponse,
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
    ScoreScriptRequest,
    ScoreScriptResponse,
    ScriptIssue,
    VerifyPromptsRequest,
    VerifyPromptsResponse,
    ClipVerification,
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

        # Guarantee acronyms are hyphenated (deterministic — no LLM, always runs)
        current_prompt = hyphenate_dialogue_acronyms(current_prompt)

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
                # Content/safety block — rephrase BEFORE text-only fallback
                logger.warning(
                    f"⚠️ {clip_label} blocked (attempt {attempt}): "
                    f"{err_str[:120]} — rephrasing and falling back to text-only"
                )
                current_prompt = rephrase_blocked_prompt(gemini_client, current_prompt, attempt)
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
        ok = stitch_clips(clip_paths, final_path, transition_sec=0.5)
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


# ── Analyse script (Step 0) ───────────────────────────────────────────────────

@app.post("/api/analyse-script", response_model=AnalyseScriptResponse)
async def analyse_script(request: AnalyseScriptRequest):
    """
    Step 0 — Analyse the raw script and return a production brief + improved script.
    The improved script is shown to the user for review before clip prompts are built.
    """
    gemini_client, _ = _get_clients()
    try:
        production_brief, improved_script = analyse_script_for_production(
            gemini_client, request.script, request.num_clips,
            provider=request.provider if request.provider != "gemini" else None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Script analysis failed: {e}")

    return AnalyseScriptResponse(
        production_brief=production_brief,
        improved_script=improved_script,
    )


# ── Generate prompts ──────────────────────────────────────────────────────────

@app.post("/api/generate-prompts", response_model=GeneratePromptsResponse)
async def generate_prompts(request: GeneratePromptsRequest):
    """
    Accepts the script, character data, and settings.
    Runs build_clip_prompts and returns the array of prompts for user review.
    """
    gemini_client, _ = _get_clients()

    character_sheet = request.character_sheet
    char_sheet_provider = request.character_sheet_provider if request.character_sheet_provider != "gemini" else None
    if not request.has_photos and not character_sheet:
        try:
            character_sheet = build_character_sheet(
                gemini_client, request.script, provider=char_sheet_provider
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Character sheet generation failed: {e}")

    photo_analyses_dict = {
        name: {"appearance": data.appearance, "outfit": data.outfit}
        for name, data in request.photo_analyses.items()
    }

    clip_provider = request.clip_build_provider if request.clip_build_provider != "gemini" else None
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
            production_brief=request.production_brief,
            provider=clip_provider,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prompt generation failed: {e}")

    return GeneratePromptsResponse(
        clips=[ClipPrompt(**c) for c in clips],
        character_sheet=character_sheet,
        production_brief=request.production_brief,
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




 
@app.post("/api/verify-prompts", response_model=VerifyPromptsResponse)
async def verify_prompts(request: VerifyPromptsRequest):
    """
    Phase 4.5 — Gemini Verification Agent.
    Sends all clip prompts to Gemini for emotional enrichment audit.
    Returns per-clip status, issues found, and improved prompts if needed.
    """
    import json

    gemini_client, _ = _get_clients()
    script_context = request.script if request.script else "Not provided"
    verify_provider = request.provider  # "anthropic" | "gemini"

    from .ai_router import generate_text as _gen_text

    # Per-clip single-object output schema avoids truncation.
    # Sending all clips in one request causes Gemini to exceed its output token
    # budget mid-JSON (especially with long Hindi prompts), producing unterminated
    # strings.  One call per clip keeps each response small and well within limits.
    PER_CLIP_OUTPUT_FORMAT = """\nReturn a single JSON object (no array wrapper):
{
  "clip": <clip number>,
  "status": "approved" or "improved",
  "issues": ["..."],
  "clip_score": <0-100 weighted score>,
  "improved_prompt": "Full corrected prompt, identical to input if approved."
}"""

    verified_clips: list[ClipVerification] = []
    issues_summary: list[str] = []

    try:
        for clip in request.clips:
            user_message = (
                f"Audit CLIP {clip.clip} — {clip.scene_summary}\n\n"
                f"ORIGINAL SCRIPT CONTEXT:\n{script_context}\n\n"
                f"CLIP PROMPT:\n{clip.prompt}\n\n"
                f"Check every rule strictly.{PER_CLIP_OUTPUT_FORMAT}"
            )

            if verify_provider == "anthropic":
                raw = _gen_text(
                    task="prompt_verification",
                    system_prompt=GEMINI_PROMPT_AUDITOR,
                    user_message=user_message,
                    provider="anthropic",
                    temperature=0.3,
                    max_tokens=16384,
                )
            else:
                response = gemini_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=user_message,
                    config=types.GenerateContentConfig(
                        system_instruction=GEMINI_PROMPT_AUDITOR,
                        response_mime_type="application/json",
                        temperature=0.3,
                        max_output_tokens=16384,
                    ),
                )
                raw = (response.text or "").strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            # Parse — tolerate any malformed / truncated output
            parsed: dict = {}
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                pass

            # Unwrap {"clips": [...]} if Gemini ignored the single-object instruction
            if isinstance(parsed, list):
                parsed = parsed[0] if parsed else {}
            elif "clips" in parsed and isinstance(parsed["clips"], list) and parsed["clips"]:
                parsed = parsed["clips"][0]

            # Extract fields ClipVerification expects — ignore all extras.
            # Coerce types so Pydantic never sees an unexpected value.
            issues_raw = parsed.get("issues", [])
            if isinstance(issues_raw, str):
                issues_raw = [issues_raw] if issues_raw else []
            elif not isinstance(issues_raw, list):
                issues_raw = []

            # clip_score: use Gemini's weighted score if provided and valid
            raw_score = parsed.get("clip_score")
            try:
                clip_score = max(0, min(100, int(raw_score))) if raw_score is not None else None
            except (TypeError, ValueError):
                clip_score = None

            # Fall back to issue-count estimate if Gemini didn't return a score
            if clip_score is None:
                clip_score = max(0, 100 - len(issues_raw) * 10)

            c = {
                "clip": int(parsed.get("clip") or clip.clip),
                "status": str(parsed.get("status") or "approved"),
                "issues": [str(i) for i in issues_raw],
                "clip_score": clip_score,
                "improved_prompt": hyphenate_dialogue_acronyms(
                    str(parsed.get("improved_prompt") or "") or clip.prompt
                ),
            }
            verified_clips.append(ClipVerification(**c))
            if c.get("issues"):
                issues_summary.append(f"Clip {clip.clip}: {'; '.join(c['issues'][:2])}")

        improved_count = sum(1 for c in verified_clips if c.status == "improved")
        # Weighted overall_score: average of per-clip scores from Gemini's weighted evaluation
        if verified_clips:
            overall_score = max(0, round(sum(c.clip_score for c in verified_clips) / len(verified_clips)))
        else:
            overall_score = 100
        summary = (
            f"{improved_count}/{len(verified_clips)} clips improved. Score: {overall_score}/100. "
            + (" | ".join(issues_summary[:3]) if issues_summary else "All clips passed.")
        )

        return VerifyPromptsResponse(
            clips=verified_clips,
            overall_score=overall_score,
            summary=summary,
        )

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Gemini returned invalid JSON: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Verification failed: {e}")


# ── Score script — 13-rule CPI audit ─────────────────────────────────────────

@app.post("/api/score-script", response_model=ScoreScriptResponse)
async def score_script(request: ScoreScriptRequest):
    """
    13-rule CPI scoring for ad scripts.
    Returns score, per-rule issues with exact lines, and an improved script.
    Uses Anthropic Claude by default (configurable via provider param).
    Separate from /api/analyse-script (production brief + 8-dimension rewrite).
    """
    from .script_analyser import analyse_script as _score

    try:
        result = await _score(
            raw_script=request.script,
            provider=request.provider,
        )
        return ScoreScriptResponse(
            score=result.get("score", 0),
            issues=[ScriptIssue(**i) for i in result.get("issues", [])],
            improved_script=result.get("improved_script", request.script),
            hook_type=result.get("hook_type", ""),
            tier2_score=result.get("tier2_score", 0),
            tier2_notes=result.get("tier2_notes", ""),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Script scoring failed: {e}")

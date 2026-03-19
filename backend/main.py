"""
SuperLiving Ad Generator API
AI logic -> ai_engine.py | FFmpeg logic -> video_engine.py
Production-ready: AWS deployment, parallel workers, security hardening
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer
from google import genai
from google.genai import types
from starlette.middleware.base import BaseHTTPMiddleware

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
    JobStatusResponse,
    RegenerateClipsRequest,
    RegenerateClipsResponse,
)
from .video_engine import concat_with_normalized_cta, stitch_clips

load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TMP = tempfile.gettempdir()
MAX_RETRIES = 3

# ── Parallel job state ─────────────────────────────────────────────────────
# Stores {job_id: {"status": str, "progress": int, "result": dict|None, "error": str|None}}
_jobs: dict[str, dict] = {}
# Thread pool for background video generation (up to 4 parallel workers)
_executor = ThreadPoolExecutor(max_workers=4)


# ── Security: allowed file types for uploads ───────────────────────────────
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_UPLOAD_SIZE_MB = 10


def _unique_video_path(tag: str) -> str:
    return os.path.join(
        TMP,
        f"video_{tag}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.mp4",
    )


def _sanitize_filename(filename: str) -> str:
    """Strip path traversal and dangerous chars from uploaded filenames."""
    return re.sub(r"[^\w.\-]", "_", os.path.basename(filename))


# ── Rate limiting (simple in-memory, replace with Redis for multi-instance) ─
_rate_limit: dict[str, list[float]] = {}
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "30"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks
        if request.url.path == "/api/health":
            return await call_next(request)

        client_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
        now = time.time()
        window_start = now - RATE_LIMIT_WINDOW

        if client_ip not in _rate_limit:
            _rate_limit[client_ip] = []

        # Purge old entries
        _rate_limit[client_ip] = [t for t in _rate_limit[client_ip] if t > window_start]

        if len(_rate_limit[client_ip]) >= RATE_LIMIT_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please slow down."},
            )

        _rate_limit[client_ip].append(now)
        return await call_next(request)


# ── App lifecycle ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 SuperLiving API starting up")
    yield
    logger.info("🛑 SuperLiving API shutting down — cleaning up thread pool")
    _executor.shutdown(wait=False)


app = FastAPI(
    title="SuperLiving Ad Generator API",
    version="2.0.0",
    description="AI-powered video ad generation — production build",
    lifespan=lifespan,
    # Disable docs in production for security
    docs_url=None if os.getenv("ENVIRONMENT") == "production" else "/docs",
    redoc_url=None if os.getenv("ENVIRONMENT") == "production" else "/redoc",
)

# ── CORS ───────────────────────────────────────────────────────────────────
_allowed_origins_raw = os.getenv("FRONTEND_URL", "http://localhost:3000")
_allowed_origins = [o.strip() for o in _allowed_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(RateLimitMiddleware)


# ── Helpers ────────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=500, detail="GOOGLE_API_KEY not configured on server.")
    return key


def _get_clients() -> tuple:
    api_key = _get_api_key()
    gemini_client = genai.Client(api_key=api_key)
    video_client = genai.Client(api_key=api_key, http_options={"api_version": "v1alpha"})
    return gemini_client, video_client


def _update_job(job_id: str, **kwargs):
    if job_id in _jobs:
        _jobs[job_id].update(kwargs)


# ── Core video generation logic (runs in thread pool) ─────────────────────

def _run_generate_video_job(
    job_id: str,
    clips: list,
    veo_model: str,
    aspect_ratio: str,
    num_clips: int,
    characters: list = None,
):
    """
    Blocking video generation — executed in a background thread.
    Updates _jobs[job_id] throughout.
    """
    try:
        gemini_client, video_client = _get_clients()
        api_key = _get_api_key()

        clip_paths: list[str] = []
        total = len(clips)

        anchor_image_b64 = ""
        if characters:
            for char in characters:
                if getattr(char, "reference_image_base64", ""):
                    anchor_image_b64 = char.reference_image_base64
                    break

        for i, clip in enumerate(clips):
            _update_job(
                job_id,
                status="generating",
                step=f"Generating clip {i + 1}/{total}: {clip.scene_summary}",
                progress=int((i / total) * 80),
            )

            current_prompt = clip.prompt
            operation = None

            # Pre-sanitize
            try:
                sanitized = sanitize_prompt_for_veo(gemini_client, current_prompt, clip.clip)
                if sanitized and len(sanitized) > 100:
                    current_prompt = sanitized
            except Exception as san_err:
                logger.warning(f"Sanitizer failed ({san_err}) — using original prompt")

            for attempt in range(1, MAX_RETRIES + 1):
                if attempt > 1:
                    current_prompt = rephrase_blocked_prompt(gemini_client, current_prompt, attempt)

                try:
                    if i == 0:
                        if anchor_image_b64:
                            operation = generate_clip_from_image(
                                video_client, veo_model, current_prompt,
                                aspect_ratio, clip.clip, num_clips, anchor_image_b64,
                            )
                        else:
                            operation = generate_clip_text_only(
                                video_client, veo_model, current_prompt,
                                aspect_ratio, clip.clip, num_clips,
                            )
                    else:
                        prev_path = clip_paths[i - 1]
                        next_summary = clips[i].scene_summary if i < len(clips) else ""
                        operation, current_prompt = generate_clip_with_frame_context(
                            video_client, gemini_client, veo_model, current_prompt,
                            aspect_ratio, clip.clip, num_clips, prev_path, next_summary,
                        )
                except Exception as gen_err:
                    err_str = str(gen_err)
                    RETRYABLE = ("503", "Deadline", "Broken pipe", "Errno 32",
                                 "ConnectionReset", "RemoteDisconnected", "Connection reset",
                                 "timed out", "timeout")
                    if any(e in err_str for e in RETRYABLE) and attempt < MAX_RETRIES:
                        wait = 15 * attempt
                        logger.warning(f"Clip {clip.clip} transient error (attempt {attempt}) — retry in {wait}s")
                        time.sleep(wait)
                        continue
                    logger.warning(f"Clip {clip.clip} failed (attempt {attempt}) — text-only fallback")
                    operation = generate_clip_text_only(
                        video_client, veo_model, current_prompt,
                        aspect_ratio, clip.clip, num_clips,
                    )

                if operation is None:
                    if attempt < MAX_RETRIES:
                        continue
                    raise RuntimeError(f"Clip {clip.clip} timed out after {MAX_RETRIES} attempts.")

                try:
                    video_obj = extract_generated_video(operation, clip.clip)
                except RaiCelebrityError:
                    operation = generate_clip_text_only(
                        video_client, veo_model, current_prompt,
                        aspect_ratio, clip.clip, num_clips,
                    )
                    video_obj = extract_generated_video(operation, clip.clip) if operation else None
                except RaiContentError:
                    video_obj = None

                if video_obj is not None:
                    break
                if attempt == MAX_RETRIES:
                    raise RuntimeError(f"Clip {clip.clip} failed after {MAX_RETRIES} attempts.")

            # Save clip
            clip_path = _unique_video_path(f"clip_{i + 1:02d}")
            video_bytes = download_video(video_obj.uri, api_key)
            with open(clip_path, "wb") as f:
                f.write(video_bytes)
            clip_paths.append(clip_path)
            logger.info(f"✅ Clip {clip.clip} saved ({len(video_bytes) // 1024} KB)")

        # Stitch
        _update_job(job_id, status="stitching", step="Stitching clips together…", progress=85)
        final_path = _unique_video_path("final")
        if len(clip_paths) > 1:
            ok = stitch_clips(clip_paths, final_path)
            if not ok:
                final_path = clip_paths[0]
        else:
            final_path = clip_paths[0]

        # Append CTA
        _update_job(job_id, step="Appending CTA…", progress=93)
        cta_appended_path = _unique_video_path("final_with_cta")
        base_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(base_dir)
        cta_video_path = os.path.join(project_root, "assets", "cta.mp4")

        if os.path.exists(cta_video_path):
            cta_success = concat_with_normalized_cta(final_path, cta_video_path, cta_appended_path)
            if cta_success:
                final_path = cta_appended_path

        _update_job(
            job_id,
            status="done",
            step="Complete!",
            progress=100,
            result={
                "video_url": f"/api/video/{os.path.basename(final_path)}",
                "clip_paths": clip_paths,
                "message": f"Successfully generated {num_clips} clip(s).",
            },
        )

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)
        _update_job(job_id, status="error", step="Failed", error=str(e))


# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "environment": os.getenv("ENVIRONMENT", "development"),
        "active_jobs": sum(1 for j in _jobs.values() if j["status"] in ("pending", "generating", "stitching")),
    }


@app.post("/api/generate-video", response_model=dict)
async def generate_video(request: GenerateVideoRequest):
    """
    Enqueues a video generation job and returns a job_id immediately.
    Poll /api/jobs/{job_id} for status.
    Supports up to 4 parallel jobs via thread pool.
    """
    job_id = uuid.uuid4().hex

    arMap = {
        "9:16 (Reels / Shorts)": "9:16",
        "16:9 (YouTube / Landscape)": "16:9",
    }
    ar = arMap.get(request.aspect_ratio, request.aspect_ratio)

    _jobs[job_id] = {
        "status": "pending",
        "step": "Queued…",
        "progress": 0,
        "result": None,
        "error": None,
    }

    _executor.submit(
        _run_generate_video_job,
        job_id,
        request.clips,
        request.veo_model,
        ar,
        request.num_clips,
        getattr(request, "characters", None),
    )

    return {"job_id": job_id, "message": "Job queued. Poll /api/jobs/{job_id} for status."}


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Poll this endpoint to track async video generation progress."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobStatusResponse(job_id=job_id, **_jobs[job_id])


@app.post("/api/agentic-pipeline", response_model=AgenticPipelineResponse)
async def agentic_pipeline(request: AgenticPipelineRequest):
    gemini_client, _ = _get_clients()
    api_key = _get_api_key()

    logger.info("🎬 Phase 1 — Parsing characters…")
    try:
        characters_json = parse_script_for_characters(gemini_client, request.script)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Phase 1 failed: {e}")

    logger.info("🖼️ Phase 2 — Generating reference images…")
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
            logger.warning(f"Imagen failed for {char.get('name', '?')}: {e}")
        character_profiles.append(CharacterProfile(
            id=char.get("id", ""),
            name=char.get("name", ""),
            physical_baseline=char.get("physical_baseline", ""),
            outfit=char.get("outfit", ""),
            reference_image_base64=ref_image_b64,
        ))

    logger.info("🎥 Phase 3 — Building director prompts…")
    try:
        clips = build_director_prompts(gemini_client, request.script, characters_json, request.num_clips)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Phase 3 failed: {e}")

    return AgenticPipelineResponse(
        characters=character_profiles,
        clips=[ClipPrompt(**c) for c in clips],
        message=f"Pipeline complete — {len(character_profiles)} character(s), {len(clips)} clip(s).",
    )


@app.post("/api/analyze-characters", response_model=AnalyzeCharactersResponse)
async def analyze_characters(
    names: list[str] = Form(...),
    photos: list[UploadFile] = File(...),
):
    if len(names) != len(photos):
        raise HTTPException(status_code=400, detail="Names and photos count must match.")

    # Validate uploads
    for photo in photos:
        if photo.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail=f"File type {photo.content_type} not allowed.")

    gemini_client, _ = _get_clients()
    analyses: dict[str, CharacterAnalysis] = {}

    for name, photo in zip(names, photos):
        name = name.strip()
        if not name:
            continue
        photo_bytes = await photo.read()
        if len(photo_bytes) > MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"Photo for {name} exceeds {MAX_UPLOAD_SIZE_MB}MB limit.")
        mime_type = photo.content_type or "image/jpeg"
        try:
            result = analyze_character_photo(gemini_client, name, photo_bytes, mime_type)
            analyses[name] = CharacterAnalysis(**result)
        except Exception as e:
            logger.warning(f"Could not analyse {name}: {e}")
            analyses[name] = CharacterAnalysis(appearance="", outfit="")

    return AnalyzeCharactersResponse(analyses=analyses)


@app.post("/api/generate-prompts", response_model=GeneratePromptsResponse)
async def generate_prompts(request: GeneratePromptsRequest):
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

    return GeneratePromptsResponse(clips=[ClipPrompt(**c) for c in clips], character_sheet=character_sheet)


@app.post("/api/regenerate-clips", response_model=dict)
async def regenerate_clips(request: RegenerateClipsRequest):
    """Async regeneration — returns job_id, poll /api/jobs/{job_id}."""
    job_id = uuid.uuid4().hex

    arMap = {
        "9:16 (Reels / Shorts)": "9:16",
        "16:9 (YouTube / Landscape)": "16:9",
    }
    ar = arMap.get(request.aspect_ratio, request.aspect_ratio)

    clips_to_regen = [request.clips[i] for i in sorted(request.clip_indices)]

    _jobs[job_id] = {
        "status": "pending",
        "step": "Queued for regeneration…",
        "progress": 0,
        "result": None,
        "error": None,
    }

    def _regen_job():
        try:
            gemini_client, video_client = _get_clients()
            api_key = _get_api_key()
            clip_paths = list(request.clip_paths)
            total = len(request.clip_indices)

            for step_idx, idx in enumerate(sorted(request.clip_indices)):
                clip = request.clips[idx]
                _update_job(
                    job_id,
                    status="generating",
                    step=f"Regenerating clip {clip.clip}/{request.num_clips}…",
                    progress=int((step_idx / total) * 80),
                )
                current_prompt = clip.prompt

                try:
                    sanitized = sanitize_prompt_for_veo(gemini_client, current_prompt, clip.clip)
                    if sanitized and len(sanitized) > 100:
                        current_prompt = sanitized
                except Exception:
                    pass

                operation = None
                for attempt in range(1, MAX_RETRIES + 1):
                    if attempt > 1:
                        current_prompt = rephrase_blocked_prompt(gemini_client, current_prompt, attempt)
                    try:
                        if idx == 0:
                            operation = generate_clip_text_only(
                                video_client, request.veo_model, current_prompt,
                                ar, clip.clip, request.num_clips,
                            )
                        else:
                            prev_path = clip_paths[idx - 1]
                            operation, current_prompt = generate_clip_with_frame_context(
                                video_client, gemini_client, request.veo_model, current_prompt,
                                ar, clip.clip, request.num_clips, prev_path, clip.scene_summary,
                            )
                    except Exception as gen_err:
                        logger.warning(f"Regen clip {clip.clip} failed: {gen_err}")
                        operation = generate_clip_text_only(
                            video_client, request.veo_model, current_prompt,
                            ar, clip.clip, request.num_clips,
                        )

                    if operation is None:
                        continue

                    try:
                        video_obj = extract_generated_video(operation, clip.clip)
                    except (RaiCelebrityError, RaiContentError):
                        video_obj = None

                    if video_obj is not None:
                        break

                clip_path = _unique_video_path(f"clip_{idx + 1:02d}")
                video_bytes = download_video(video_obj.uri, api_key)
                with open(clip_path, "wb") as f:
                    f.write(video_bytes)
                clip_paths[idx] = clip_path

            _update_job(job_id, step="Re-stitching…", progress=88)
            final_path = _unique_video_path("regen_final")
            if len(clip_paths) > 1:
                ok = stitch_clips(clip_paths, final_path)
                if not ok:
                    final_path = clip_paths[0]
            else:
                final_path = clip_paths[0]

            base_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(base_dir)
            cta_video_path = os.path.join(project_root, "assets", "cta.mp4")
            cta_appended_path = _unique_video_path("regen_with_cta")
            if os.path.exists(cta_video_path):
                cta_success = concat_with_normalized_cta(final_path, cta_video_path, cta_appended_path)
                if cta_success:
                    final_path = cta_appended_path

            _update_job(
                job_id,
                status="done",
                step="Complete!",
                progress=100,
                result={
                    "video_url": f"/api/video/{os.path.basename(final_path)}",
                    "clip_paths": clip_paths,
                    "message": f"Regenerated {len(request.clip_indices)} clip(s).",
                },
            )
        except Exception as e:
            logger.error(f"Regen job {job_id} failed: {e}", exc_info=True)
            _update_job(job_id, status="error", step="Failed", error=str(e))

    _executor.submit(_regen_job)
    return {"job_id": job_id, "message": "Regeneration queued."}


@app.get("/api/video/{filename}")
async def serve_video(filename: str):
    """Serve generated video — filename validated against path traversal."""
    # Strict validation: only alphanumeric, dots, underscores, hyphens
    if not re.match(r"^[\w.\-]+\.mp4$", filename):
        raise HTTPException(status_code=400, detail="Invalid filename.")

    path = os.path.join(TMP, filename)
    # Ensure the resolved path is still inside TMP (prevents traversal)
    if not os.path.realpath(path).startswith(os.path.realpath(TMP)):
        raise HTTPException(status_code=400, detail="Invalid path.")

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Video file not found.")

    return FileResponse(
        path,
        media_type="video/mp4",
        filename=filename,
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ── Global error handler ───────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.url.path}: {exc}", exc_info=True)
    # Don't leak internal details in production
    detail = str(exc) if os.getenv("ENVIRONMENT") != "production" else "Internal server error."
    return JSONResponse(status_code=500, content={"detail": detail})
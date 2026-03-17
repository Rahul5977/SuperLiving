"""
AI logic -> ai_engine.py
FFmpeg logic -> video_engine.py.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid

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
)
from .video_engine import stitch_clips

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TMP = tempfile.gettempdir()

app = FastAPI(
    title="SuperLiving Ad Generator API",
    version="1.0.0",
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
    video_client = genai.Client(api_key=api_key, http_options={"api_version": "v1alpha"})
    return gemini_client, video_client


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


# agentic pipeline endpoint orchestrates the entire Phase 1-3 flow for maximum automation, returning all data needed for Phase 4 human review. This is the main "magic" endpoint that ties everything together.

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

    # Phase 1: Parse characters from script
    logger.info("🎬 Phase 1 — Parsing characters from script…")
    try:
        characters_json = parse_script_for_characters(gemini_client, request.script)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Phase 1 (Parser Agent) failed: {e}")

    # Phase 2: Generate reference images for each character
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

    # Phase 3: Build director prompts 
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


@app.post("/api/generate-prompts", response_model=GeneratePromptsResponse)
async def generate_prompts(request: GeneratePromptsRequest):
    """
    Accepts the script, character data, and settings.
    Runs build_clip_prompts and returns the array of prompts for user review.
    """
    gemini_client, _ = _get_clients()

    # Build character sheet if no photos provided
    character_sheet = request.character_sheet
    if not request.has_photos and not character_sheet:
        try:
            character_sheet = build_character_sheet(gemini_client, request.script)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Character sheet generation failed: {e}")

    # Convert photo_analyses from Pydantic to plain dict
    photo_analyses_dict = {
        name: {"appearance": data.appearance, "outfit": data.outfit}
        for name, data in request.photo_analyses.items()
    }

    try:
        clips = build_clip_prompts(
            client=gemini_client,
            script=request.script,
            extra_prompt=request.extra_prompt,
            extra_image_parts=[],  # Reference images handled via separate upload
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


@app.post("/api/generate-video", response_model=GenerateVideoResponse)
async def generate_video(request: GenerateVideoRequest):
    """
    Accepts user-reviewed prompts.
    Runs the Veo generation loop (including last-frame I2V extraction)
    and the stitch_clips function. Returns the final MP4 file URL.
    """
    gemini_client, video_client = _get_clients()
    api_key = _get_api_key()

    clip_paths: list[str] = []
    MAX_RETRIES = 3

    # ── Resolve anchor image for Clip 1 (first character's reference photo) ──
    anchor_image_b64: str = ""
    if hasattr(request, "characters") and request.characters:
        for char in request.characters:
            if getattr(char, "reference_image_base64", ""):
                anchor_image_b64 = char.reference_image_base64
                logger.info(f"🖼️ Clip 1 anchor image found for '{char.name}'")
                break

    try:
        for i, clip in enumerate(request.clips):
            logger.info(f"🎥 Clip {clip.clip}/{request.num_clips}: {clip.scene_summary}")
            current_prompt = clip.prompt
            operation = None

            # ── Pre-sanitize before first attempt ─────────────────────────
            try:
                sanitized = sanitize_prompt_for_veo(
                    gemini_client, current_prompt, clip.clip
                )
                if sanitized and len(sanitized) > 100:
                    current_prompt = sanitized
            except Exception as san_err:
                logger.warning(f"⚠️ Sanitizer failed ({san_err}) — using original prompt")

            for attempt in range(1, MAX_RETRIES + 1):
                if attempt > 1:
                    current_prompt = rephrase_blocked_prompt(
                        gemini_client, current_prompt, attempt
                    )

                try:
                    if i == 0:
                        # BUG 2 FIX — Clip 1: use anchor reference image if available,
                        # otherwise fall back to text-only.
                        if anchor_image_b64:
                            logger.info("🖼️ Clip 1: generating from anchor reference image (I2V)")
                            operation = generate_clip_from_image(
                                video_client, request.veo_model, current_prompt,
                                request.aspect_ratio, clip.clip, request.num_clips,
                                anchor_image_b64,
                            )
                        else:
                            logger.info("📝 Clip 1: no anchor image — using text-only generation")
                            operation = generate_clip_text_only(
                                video_client, request.veo_model, current_prompt,
                                request.aspect_ratio, clip.clip, request.num_clips,
                            )
                    else:
                        # Clips 2+: last-frame I2V
                        prev_path = clip_paths[i - 1]
                        next_summary = request.clips[i].scene_summary if i < len(request.clips) else ""
                        operation, current_prompt = generate_clip_with_frame_context(
                            video_client, gemini_client,
                            request.veo_model, current_prompt, request.aspect_ratio,
                            clip.clip, request.num_clips,
                            prev_path, next_summary,
                        )
                except Exception as gen_err:
                    err_str = str(gen_err)
                    # BUG 1 FIX — 503 / Deadline: sleep and retry the SAME function;
                    # do NOT drop to text-only and lose the image context.
                    if ("503" in err_str or "Deadline" in err_str) and attempt < MAX_RETRIES:
                        logger.warning(
                            f"⚠️ Clip {clip.clip} transient error (attempt {attempt}): "
                            f"{err_str[:120]} — sleeping 10s and retrying with same context…"
                        )
                        time.sleep(10)
                        continue
                    # Non-503 error, or retries exhausted — fall back to text-only
                    logger.warning(
                        f"⚠️ Clip {clip.clip} generation failed (attempt {attempt}): "
                        f"{err_str[:120]} — falling back to text-only"
                    )
                    operation = generate_clip_text_only(
                        video_client, request.veo_model, current_prompt,
                        request.aspect_ratio, clip.clip, request.num_clips,
                    )

                if operation is None:
                    if attempt < MAX_RETRIES:
                        continue
                    raise HTTPException(
                        status_code=500,
                        detail=f"Clip {clip.clip} timed out after {MAX_RETRIES} attempts.",
                    )

                try:
                    video_obj = extract_generated_video(operation, clip.clip)
                except RaiCelebrityError:
                    operation = generate_clip_text_only(
                        video_client, request.veo_model, current_prompt,
                        request.aspect_ratio, clip.clip, request.num_clips,
                    )
                    if operation is None:
                        video_obj = None
                    else:
                        try:
                            video_obj = extract_generated_video(operation, clip.clip)
                        except (RaiCelebrityError, RaiContentError):
                            video_obj = None
                except RaiContentError:
                    video_obj = None

                if video_obj is not None:
                    break

                if attempt == MAX_RETRIES:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Clip {clip.clip} failed after {MAX_RETRIES} attempts.",
                    )

            # ── Save clip ─────────────────────────────────────────────────
            clip_path = os.path.join(TMP, f"superliving_clip_{i+1:02d}.mp4")
            video_bytes = download_video(video_obj.uri, api_key)
            with open(clip_path, "wb") as f:
                f.write(video_bytes)
            clip_paths.append(clip_path)
            logger.info(f"✅ Clip {clip.clip} saved ({len(video_bytes)//1024} KB)")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Video generation error: {e}")

    # ── Stitch ────────────────────────────────────────────────────────────
    final_path = os.path.join(TMP, "superliving_final_ad.mp4")
    if len(clip_paths) > 1:
        ok = stitch_clips(clip_paths, final_path)
        if not ok:
            final_path = clip_paths[0]
    else:
        final_path = clip_paths[0]

    return GenerateVideoResponse(
        video_url=f"/api/video/{os.path.basename(final_path)}",
        clip_paths=clip_paths,
        message=f"Successfully generated {request.num_clips} clip(s).",
    )


@app.post("/api/regenerate-clips", response_model=RegenerateClipsResponse)
async def regenerate_clips(request: RegenerateClipsRequest):
    """
    Accepts specific clip indices, regenerates only those,
    runs stitch_clips again, and returns the new video.
    """
    gemini_client, video_client = _get_clients()
    api_key = _get_api_key()

    clip_paths = list(request.clip_paths)
    MAX_RETRIES = 3

    try:
        for idx in sorted(request.clip_indices):
            i = idx
            clip = request.clips[i]
            logger.info(f"🔄 Regenerating clip {clip.clip}/{request.num_clips}: {clip.scene_summary}")
            current_prompt = clip.prompt
            operation = None

            # ── Pre-sanitize ──────────────────────────────────────────
            try:
                sanitized = sanitize_prompt_for_veo(
                    gemini_client, current_prompt, clip.clip
                )
                if sanitized and len(sanitized) > 100:
                    current_prompt = sanitized
            except Exception:
                pass

            for attempt in range(1, MAX_RETRIES + 1):
                if attempt > 1:
                    current_prompt = rephrase_blocked_prompt(
                        gemini_client, current_prompt, attempt
                    )

                try:
                    if i == 0:
                        # BUG 2 FIX — Clip 1: use anchor reference image if available.
                        anchor_image_b64 = ""
                        if hasattr(request, "characters") and request.characters:
                            for char in request.characters:
                                if getattr(char, "reference_image_base64", ""):
                                    anchor_image_b64 = char.reference_image_base64
                                    break
                        if anchor_image_b64:
                            logger.info("🖼️ Regen Clip 1: generating from anchor reference image (I2V)")
                            operation = generate_clip_from_image(
                                video_client, request.veo_model, current_prompt,
                                request.aspect_ratio, clip.clip, request.num_clips,
                                anchor_image_b64,
                            )
                        else:
                            logger.info("📝 Regen Clip 1: no anchor image — using text-only generation")
                            operation = generate_clip_text_only(
                                video_client, request.veo_model, current_prompt,
                                request.aspect_ratio, clip.clip, request.num_clips,
                            )
                    else:
                        prev_path = clip_paths[i - 1]
                        next_summary = request.clips[i].scene_summary
                        operation, current_prompt = generate_clip_with_frame_context(
                            video_client, gemini_client,
                            request.veo_model, current_prompt, request.aspect_ratio,
                            clip.clip, request.num_clips,
                            prev_path, next_summary,
                        )
                except Exception as gen_err:
                    err_str = str(gen_err)
                    # BUG 1 FIX — 503 / Deadline: sleep and retry the SAME function;
                    # do NOT drop to text-only and lose the image context.
                    if ("503" in err_str or "Deadline" in err_str) and attempt < MAX_RETRIES:
                        logger.warning(
                            f"⚠️ Regen Clip {clip.clip} transient error (attempt {attempt}): "
                            f"{err_str[:120]} — sleeping 10s and retrying with same context…"
                        )
                        time.sleep(10)
                        continue
                    # Non-503 error, or retries exhausted — fall back to text-only
                    logger.warning(f"⚠️ Regen Clip {clip.clip} failed: {err_str[:120]} — text-only fallback")
                    operation = generate_clip_text_only(
                        video_client, request.veo_model, current_prompt,
                        request.aspect_ratio, clip.clip, request.num_clips,
                    )

                if operation is None:
                    if attempt < MAX_RETRIES:
                        continue
                    raise HTTPException(
                        status_code=500,
                        detail=f"Clip {clip.clip} timed out after {MAX_RETRIES} attempts.",
                    )

                try:
                    video_obj = extract_generated_video(operation, clip.clip)
                except RaiCelebrityError:
                    operation = generate_clip_text_only(
                        video_client, request.veo_model, current_prompt,
                        request.aspect_ratio, clip.clip, request.num_clips,
                    )
                    if operation is None:
                        video_obj = None
                    else:
                        try:
                            video_obj = extract_generated_video(operation, clip.clip)
                        except (RaiCelebrityError, RaiContentError):
                            video_obj = None
                except RaiContentError:
                    video_obj = None

                if video_obj is not None:
                    break
                if attempt == MAX_RETRIES:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Clip {clip.clip} failed after {MAX_RETRIES} attempts.",
                    )

            # ── Save regenerated clip ─────────────────────────────────
            clip_path = os.path.join(TMP, f"superliving_clip_{i+1:02d}.mp4")
            video_bytes = download_video(video_obj.uri, api_key)
            with open(clip_path, "wb") as f:
                f.write(video_bytes)
            clip_paths[i] = clip_path
            logger.info(f"✅ Clip {clip.clip} regenerated ({len(video_bytes)//1024} KB)")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Regeneration error: {e}")

    # ── Re-stitch ─────────────────────────────────────────────────────────
    final_path = os.path.join(TMP, "superliving_final_ad.mp4")
    if len(clip_paths) > 1:
        ok = stitch_clips(clip_paths, final_path)
        if not ok:
            final_path = clip_paths[0]
    else:
        final_path = clip_paths[0]

    return RegenerateClipsResponse(
        video_url=f"/api/video/{os.path.basename(final_path)}",
        clip_paths=clip_paths,
        message=f"Successfully regenerated {len(request.clip_indices)} clip(s).",
    )


# Serve generated videos 
@app.get("/api/video/{filename}")
async def serve_video(filename: str):
    """Serve a generated video file from the temp directory."""
    path = os.path.join(TMP, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Video file not found.")
    return FileResponse(path, media_type="video/mp4", filename=filename)
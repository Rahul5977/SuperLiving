"""Pydantic models for request/response validation."""

import json
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


# ── Shared / Sub-models ──────────────────────────────────────────────────────

class CharacterAnalysis(BaseModel):
    appearance: str = ""
    outfit: str = ""


class ClipPrompt(BaseModel):
    clip: int
    scene_summary: str
    last_frame: str = ""
    prompt: str

    @field_validator("prompt", mode="before")
    @classmethod
    def coerce_prompt_to_str(cls, v: Any) -> str:
        """
        Gemini occasionally returns the prompt field as a structured dict
        (e.g. {'OUTFIT & APPEARANCE': '...', 'ACTION': '...'}) instead of a
        flat string.  Flatten it so Pydantic validation never fails.
        """
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            # Join each section as "KEY:\nVALUE" blocks
            parts = []
            for key, val in v.items():
                parts.append(f"{key}:\n{val}" if val else str(key))
            return "\n\n".join(parts)
        # Fallback for any other unexpected type
        return json.dumps(v, ensure_ascii=False)

    @field_validator("scene_summary", "last_frame", mode="before")
    @classmethod
    def coerce_str_fields(cls, v: Any) -> str:
        """Guard the other string fields against the same dict-return issue."""
        if isinstance(v, str):
            return v
        if v is None:
            return ""
        return str(v)


# ── POST /api/analyze-characters ─────────────────────────────────────────────

class AnalyzeCharactersResponse(BaseModel):
    analyses: dict[str, CharacterAnalysis] = Field(default_factory=dict)


# ── POST /api/generate-prompts ───────────────────────────────────────────────

class GeneratePromptsRequest(BaseModel):
    script: str = Field(..., min_length=1)
    extra_prompt: str = ""
    character_sheet: str = ""
    photo_analyses: dict[str, CharacterAnalysis] = Field(default_factory=dict)
    aspect_ratio: str = "9:16 (Reels / Shorts)"
    num_clips: int = Field(default=6, ge=1, le=8)
    language_note: bool = True
    has_photos: bool = False


class GeneratePromptsResponse(BaseModel):
    clips: list[ClipPrompt]
    character_sheet: str = ""


# ── POST /api/generate-video (async job) ─────────────────────────────────────

class GenerateVideoRequest(BaseModel):
    clips: list[ClipPrompt]
    veo_model: str = "veo-3.1-generate-preview"
    aspect_ratio: str = "9:16"
    num_clips: int = Field(default=6, ge=1, le=8)
    characters: list[Any] = Field(default_factory=list)


class GenerateVideoResponse(BaseModel):
    video_url: str
    clip_paths: list[str] = Field(default_factory=list)
    message: str = ""


# ── Job status (async polling) ────────────────────────────────────────────────

class JobStatusResponse(BaseModel):
    job_id: str
    status: str   # "pending" | "generating" | "stitching" | "done" | "error"
    step: str = ""
    progress: int = 0  # 0-100
    result: Optional[dict] = None
    error: Optional[str] = None


# ── POST /api/regenerate-clips ───────────────────────────────────────────────

class RegenerateClipsRequest(BaseModel):
    clip_indices: list[int] = Field(..., description="0-based indices of clips to regenerate")
    clips: list[ClipPrompt]
    clip_paths: list[str]
    veo_model: str = "veo-3.1-generate-preview"
    aspect_ratio: str = "9:16"
    num_clips: int = Field(default=6, ge=1, le=8)


class RegenerateClipsResponse(BaseModel):
    video_url: str
    clip_paths: list[str] = Field(default_factory=list)
    message: str = ""


# ── POST /api/agentic-pipeline ───────────────────────────────────────────────

class CharacterProfile(BaseModel):
    id: str
    name: str
    physical_baseline: str
    outfit: str
    reference_image_base64: str = Field(default="")


class AgenticPipelineRequest(BaseModel):
    script: str = Field(..., min_length=1)
    num_clips: int = Field(default=6, ge=1, le=8)


class AgenticPipelineResponse(BaseModel):
    characters: list[CharacterProfile]
    clips: list[ClipPrompt]
    message: str = ""
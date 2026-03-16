"""Pydantic models for request/response validation."""

from pydantic import BaseModel, Field
from typing import Optional


# ── Shared / Sub-models ──────────────────────────────────────────────────────

class CharacterAnalysis(BaseModel):
    appearance: str = ""
    outfit: str = ""


class ClipPrompt(BaseModel):
    clip: int
    scene_summary: str
    last_frame: str = ""
    prompt: str


# ── POST /api/analyze-characters ─────────────────────────────────────────────

class AnalyzeCharactersResponse(BaseModel):
    analyses: dict[str, CharacterAnalysis] = Field(
        default_factory=dict,
        description="Mapping of character name → locked appearance/outfit analysis",
    )


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


# ── POST /api/generate-video ─────────────────────────────────────────────────

class GenerateVideoRequest(BaseModel):
    clips: list[ClipPrompt]
    veo_model: str = "veo-3.1-generate-preview"
    aspect_ratio: str = "9:16"
    num_clips: int = Field(default=6, ge=1, le=8)


class GenerateVideoResponse(BaseModel):
    video_url: str
    clip_paths: list[str] = Field(default_factory=list)
    message: str = ""


# ── POST /api/regenerate-clips ───────────────────────────────────────────────

class RegenerateClipsRequest(BaseModel):
    clip_indices: list[int] = Field(
        ...,
        description="0-based indices of clips to regenerate",
    )
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
    reference_image_base64: str = Field(
        default="",
        description="Base64-encoded 9:16 reference face from Imagen (populated by Phase 2)",
    )


class AgenticPipelineRequest(BaseModel):
    script: str = Field(..., min_length=1)
    num_clips: int = Field(default=6, ge=1, le=8)


class AgenticPipelineResponse(BaseModel):
    characters: list[CharacterProfile]
    clips: list[ClipPrompt]
    message: str = ""

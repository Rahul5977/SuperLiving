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


# ── POST /api/analyse-script ──────────────────────────────────────────────────

class AnalyseScriptRequest(BaseModel):
    script: str = Field(..., min_length=1)
    num_clips: int = Field(default=6, ge=1, le=8)
    provider: str = "gemini"   # "gemini" | "anthropic"


class AnalyseScriptResponse(BaseModel):
    production_brief: str = ""
    improved_script: str = ""


# ── POST /api/score-script ────────────────────────────────────────────────────

class ScoreScriptRequest(BaseModel):
    script: str = Field(..., min_length=1)
    provider: str = "anthropic"   # "anthropic" | "gemini"


class ScriptIssue(BaseModel):
    rule: int
    severity: str
    description: str
    original_line: str = ""
    fixed_line: str = ""


class ScoreScriptResponse(BaseModel):
    score: int
    issues: list[ScriptIssue]
    improved_script: str
    hook_type: str = ""
    tier2_score: int = 0
    tier2_notes: str = ""


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
    production_brief: str = ""  # pre-computed by /api/analyse-script
    # Model routing — override defaults in ai_router.py
    clip_build_provider: str = "gemini"      # "gemini" | "anthropic"
    character_sheet_provider: str = "gemini" # "gemini" | "anthropic"


class GeneratePromptsResponse(BaseModel):
    clips: list[ClipPrompt]
    character_sheet: str = ""
    production_brief: str = ""


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


# POST /api/verify-prompts
class VerifyPromptsRequest(BaseModel):
    clips: list[ClipPrompt]
    script: str = ""
    provider: str = "anthropic"   # "anthropic" | "gemini"
 
 
class ClipVerification(BaseModel):
    clip: int
    status: str             # "approved" | "improved"
    issues: list[str]       # list of what was wrong
    clip_score: int = 100   # 0-100 weighted score (see auditor scoring weights)
    improved_prompt: str    # same as original if approved, fixed if improved
 
 
class VerifyPromptsResponse(BaseModel):
    clips: list[ClipVerification]
    overall_score: int      # 0-100
    summary: str            # one-line verdict
 
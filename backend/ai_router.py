"""
ai_router.py — Unified AI client router for SuperLiving Flash Tool.

Routes each task to the appropriate model based on configuration.
Supports Anthropic (Claude) and Google (Gemini) interchangeably
for text tasks. Veo video generation always uses Google.

DEFAULTS:
  script_analysis    → Claude claude-sonnet-4-5 (best Hinglish nuance)
  prompt_verification → Claude claude-sonnet-4-5 (best rule adherence)
  clip_prompt_build  → Gemini gemini-2.5-pro (best long Hindi generation)
  sanitization       → Gemini gemini-2.0-flash (fast, cheap)
  character_analysis → Gemini gemini-2.5-pro (multimodal, image input)
  character_sheet    → Gemini gemini-2.5-pro (multimodal context)

Override any default by passing provider="anthropic" or provider="gemini"
to any function that accepts it.
"""

import os
import anthropic
from google import genai
from google.genai import types

# ── Model name constants ──────────────────────────────────────────────────────

# Anthropic
CLAUDE_SONNET = "claude-sonnet-4-5"
CLAUDE_HAIKU  = "claude-haiku-4-5"     # fast + cheap, for simple tasks

# Gemini
GEMINI_PRO   = "gemini-2.5-pro"
GEMINI_FLASH = "gemini-2.0-flash"      # fast + cheap

# Task → default provider mapping
TASK_DEFAULTS = {
    "script_analysis":     "anthropic",
    "prompt_verification": "anthropic",
    "clip_prompt_build":   "gemini",
    "sanitization":        "gemini",
    "character_analysis":  "gemini",
    "character_sheet":     "gemini",
}

# Task → default model per provider
TASK_MODELS = {
    "script_analysis":     {"anthropic": CLAUDE_SONNET, "gemini": GEMINI_PRO},
    "prompt_verification": {"anthropic": CLAUDE_SONNET, "gemini": GEMINI_FLASH},
    "clip_prompt_build":   {"anthropic": CLAUDE_SONNET, "gemini": GEMINI_PRO},
    "sanitization":        {"anthropic": CLAUDE_HAIKU,  "gemini": GEMINI_FLASH},
    "character_analysis":  {"anthropic": CLAUDE_SONNET, "gemini": GEMINI_PRO},
    "character_sheet":     {"anthropic": CLAUDE_SONNET, "gemini": GEMINI_PRO},
}


# ── Client factories ──────────────────────────────────────────────────────────

def get_anthropic_client() -> anthropic.Anthropic:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")
    return anthropic.Anthropic(api_key=key)


def get_gemini_client() -> genai.Client:
    key = os.getenv("GOOGLE_API_KEY", "")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY not set in environment")
    return genai.Client(api_key=key)


def get_video_client() -> genai.Client:
    """Veo video generation always uses Gemini."""
    key = os.getenv("GOOGLE_API_KEY", "")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY not set in environment")
    return genai.Client(api_key=key, http_options={"api_version": "v1alpha"})


# ── Unified text generation ───────────────────────────────────────────────────

def generate_text(
    task: str,
    system_prompt: str,
    user_message: str,
    provider: str = None,       # None = use TASK_DEFAULTS
    model: str = None,          # None = use TASK_MODELS
    temperature: float = 0.2,
    max_tokens: int = 8192,
) -> str:
    """
    Single function to call either Anthropic or Gemini for text generation.

    Usage:
        result = generate_text(
            task="script_analysis",
            system_prompt=ANALYSER_SYSTEM_PROMPT,
            user_message=f"Analyse this script:\\n{script}",
        )

    Override provider:
        result = generate_text(
            task="prompt_verification",
            system_prompt=VERIFY_PROMPT,
            user_message=clips_text,
            provider="anthropic",
        )

    Returns the raw text response string.
    """
    resolved_provider = provider or TASK_DEFAULTS.get(task, "gemini")
    resolved_model    = model    or TASK_MODELS[task][resolved_provider]

    if resolved_provider == "anthropic":
        return _call_anthropic(
            system_prompt=system_prompt,
            user_message=user_message,
            model=resolved_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    else:
        return _call_gemini(
            system_prompt=system_prompt,
            user_message=user_message,
            model=resolved_model,
            temperature=temperature,
        )


def _call_anthropic(
    system_prompt: str,
    user_message: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> str:
    client = get_anthropic_client()
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return message.content[0].text.strip()


def _call_gemini(
    system_prompt: str,
    user_message: str,
    model: str,
    temperature: float,
) -> str:
    client = get_gemini_client()
    response = client.models.generate_content(
        model=model,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
        ),
    )
    if response is None or response.text is None:
        raise RuntimeError(f"Gemini ({model}) returned empty response")
    return response.text.strip()

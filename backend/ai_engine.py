"""
AI Engine for SuperLiving ad generation.
"""

import json
import logging
import re
import time
import urllib.request

from google import genai
from google.genai import types

from .prompts import (
    CHARACTER_SHEET_SYSTEM,
    SANITIZE_VEO_SYSTEM,
    analyze_character_photo_prompt,
    build_character_sheet_prompt,
    build_clip_prompts_system,
    build_continuing_from_prompt,
    rephrase_blocked_prompt_contents,
)

logger = logging.getLogger(__name__)



# EXCEPTIONS
class RaiCelebrityError(Exception):
    """Raised when Veo rejects the I2V input image due to celebrity detection."""
    pass

class RaiContentError(Exception):
    """Raised when Veo rejects the prompt due to content policy."""
    pass



# POLLING


def poll_operation(video_client, operation, label: str):
    elapsed = 0
    poll_interval = 20
    max_wait = 720
    while not operation.done:
        mins, secs = divmod(elapsed, 60)
        t = f"{mins}m {secs}s" if mins else f"{secs}s"
        logger.info(f"⏳ {label} — {t} elapsed (typical: 3–6 min per clip)")
        time.sleep(poll_interval)
        elapsed += poll_interval
        operation = video_client.operations.get(operation)
        if elapsed >= max_wait:
            logger.error("⏰ Timed out. Try again or use fewer clips.")
            return None
    return operation



# CHARACTER ANALYSIS
def analyze_character_photo(client, name: str, photo_bytes: bytes, mime_type: str) -> dict:
    """
    Gemini Vision → two locked fields:
      - "appearance": face, hair, age, build (NOT clothing)
      - "outfit": exact garment from the photo (locked for every clip)
    """
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[
            types.Part.from_bytes(data=photo_bytes, mime_type=mime_type),
            types.Part.from_text(text=analyze_character_photo_prompt(name)),
        ],
    )
    # Safely handle None response
    if response is None or response.text is None:
        return {"appearance": "", "outfit": ""}
    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        data = json.loads(raw)
        return {"appearance": data.get("appearance", ""), "outfit": data.get("outfit", "")}
    except Exception:
        return {"appearance": raw, "outfit": ""}


def build_character_sheet(client, script: str, provider: str = None) -> str:
    """Used only when no reference photos are provided."""
    from .ai_router import generate_text
    if provider is not None:
        return generate_text(
            task="character_sheet",
            system_prompt=CHARACTER_SHEET_SYSTEM,
            user_message=f"AD SCRIPT:\n{script}",
            provider=provider,
        )
    # Default: Gemini with direct client call (preserves existing behavior)
    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=build_character_sheet_prompt(script),
    )
    if response is None or response.text is None:
        return "Character sheet generation failed — please describe characters manually."
    return response.text.strip()



# CLIP CHARACTER MATCHING
def get_clip_character_photo(clip_prompt: str, char_photos_raw: list) -> tuple:
    """Returns (photo_bytes, mime_type, name) for the most-mentioned character in this clip."""
    if not char_photos_raw:
        return None, None, None

    prompt_lower = clip_prompt.lower()
    best_bytes, best_mime, best_name = None, None, None
    best_count = 0

    for name, photo_bytes, mime_type in char_photos_raw:
        count = prompt_lower.count(name.lower())
        if count > best_count:
            best_count = count
            best_bytes, best_mime, best_name = photo_bytes, mime_type, name

    if best_bytes is None:
        best_bytes, best_mime, best_name = (
            char_photos_raw[0][1], char_photos_raw[0][2], char_photos_raw[0][0]
        )

    return best_bytes, best_mime, best_name

# PROMPT GENERATION


def analyse_script_for_production(client, script: str, num_clips: int, provider: str = None) -> tuple[str, str]:
    """
    Step 0 — Script Dialogue Analyst + Rewriter.
    Runs BEFORE build_clip_prompts().
    Returns (production_brief, improved_script).
    The improved script is shown to the user for review before clip prompts are built.
    The production brief is injected into the clip prompt generator.
    """
    system = """You are a script analyst and rewriter for SuperLiving — a health coaching app
for Tier 2–3 India (Raipur, Patna, Kanpur, Nagpur). You do two things in one pass:
(A) analyse the script across 8 dimensions and write a production brief, then
(B) rewrite the script to fix every problem you found.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART A — ANALYSE ACROSS 8 DIMENSIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. HOOK TYPE
   - Is clip 1's opening line a specific physical scene, a named duration, or a named object?
   - Or does it just state an emotion (scared, sad, anxious)?
   - Specific scene/duration/object = HIGH PERFORMING HOOK
   - Named emotion = WEAK HOOK
   - State which type this script has and what fix is needed.

2. HINGLISH REGISTER
   - Is the dialogue how people actually speak in Tier 2–3 cities?
   - Flag any lines that sound like written/translated Hindi, motivational posters,
     or formal grammar.
   - Examples of BAD register: "मैं सुपरलिविंग का उपयोग करता हूँ", "विश्वास रखो"
   - Examples of GOOD register: "yaar, seedha bol de", "pata nahi kya ho gaya mujhe"

3. TIER 2–3 CULTURAL TEXTURE
   - Does the script name specific social forces? (saas, bhabhi, jethani, padosi aunty,
     office wali, chacha, bhaiya, etc.)
   - Does it name a physical location? (galley, kitchen, bed, mirror, office bathroom, bus, auto)
   - Rate: RICH / MODERATE / THIN. If THIN: name which clips need more texture.

4. COACH LINE REGISTER
   - Find every line spoken by or attributed to Rishika/Rashmi/Tara/Dev/Arjun/Pankaj/Seema.
   - Does it sound like a friend from the same city, or a health website?
   - FRIEND REGISTER: "yaar, tu theek hai", "chhod na, kal se shuru karte hain"
   - WEBSITE REGISTER: "आपकी स्वास्थ्य यात्रा शुरू होती है"
   - NOTE: All coach dialogue will be converted to quoted speech in Part B.

5. EMOTIONAL ARC
   - Label the journey: GUILT → RECOGNITION → HOPE | PAIN → SEEN → CONFIDENCE |
     SHAME → NORMALISATION → RELIEF | EXHAUSTION → VALIDATION → ACTION

6. PAYOFF TYPE
   - What does the final clip deliver?
     INTERNAL REALISATION | SOCIAL PROOF MOMENT | CONFIDENCE MIRROR MOMENT | BEHAVIOUR CHANGE

7. EM-DASH / SPEECH RHYTHM AUDIT
   - Scan every dialogue line for — (em-dash) or word-connecting - (hyphen).
   - List every occurrence. If none: "CLEAN."

8. DIALOGUE WORD COUNT PER CLIP
   - Count spoken words per clip. Flag under 13 or over 20.
   - Target: 15–19 words. Format: CLIP 1: 17 words ✓ | CLIP 2: 23 words ⚠️ (trim by 4)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART B — REWRITE THE SCRIPT (fix every issue found in Part A)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Apply ALL of these fixes in the improved script:

FIX 1 — SINGLE CHARACTER ONLY (most important fix)
SuperLiving ads have EXACTLY ONE character on screen throughout the entire ad.
If the script has a coach (Rishika, Rashmi, Tara, Dev, Arjun, Pankaj, Seema)
speaking directly, convert their lines to the MAIN CHARACTER quoting them.
The coach never appears on screen — the main character tells the audience what
the coach advised them.

CONVERSION PATTERN:
  BEFORE: कोच रश्मि: "सब बंद करो। सनस्क्रीन, हल्दी-बेसन, पानी। बस।"
  AFTER:  [main character]: "(बातचीत के लहजे में, याद करते हुए) कोच रश्मि ने बोला,
          'सब बंद करो, सनस्क्रीन, हल्दी-बेसन, पानी। बस।'"

WHY: The audience connects with ONE person's story told intimately to camera.
A second character appearing breaks intimacy and causes I2V face contamination.
The quote format keeps the coach's voice authentic without them appearing.

FIX 2 — WEAK HOOK
If clip 1 just names an emotion, replace with a specific physical scene or duration.
WEAK: "मुझे बहुत डर लगता था।"
STRONG: "हर सुबह mirror देखना बंद कर दिया था — तीन महीने से।"

FIX 3 — HINGLISH REGISTER
Convert any formal/written Hindi to natural conversational Tier 2–3 speech.
Remove motivational-poster language. Make it sound like one friend talking to another.

FIX 4 — WORD COUNT
Adjust any clip dialogue that is under 13 or over 20 words to land at 15–19 words.
Never skip core meaning — expand or trim around it.

FIX 5 — SPEECH RHYTHM
Remove all — (em-dash) and word-connecting - (hyphen) from dialogue.
Replace with comma, conjunction (aur/toh/par/phir), or full stop as appropriate.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT — use EXACTLY this structure, no markdown, no extra text
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PRODUCTION BRIEF
================
HOOK TYPE: [type + strength + fix applied in improved script]
HINGLISH REGISTER: [assessment + flagged lines]
TIER 2–3 TEXTURE: [RICH/MODERATE/THIN + gaps]
COACH LINE REGISTER: [original coach lines + how converted]
EMOTIONAL ARC: [label]
PAYOFF TYPE: [type]
DIALOGUE WORD COUNTS: [per clip with ✓/⚠️]
SPEECH RHYTHM AUDIT: [dash findings or CLEAN]
DIRECTOR NOTES: [2–3 specific instructions for the clip prompt generator]

IMPROVED SCRIPT
===============
[Full rewritten script with ALL fixes applied. Keep the same clip structure.
Format each clip as: CLIP N\n[character]: "[dialogue]"]"""

    if provider is not None:
        from .ai_router import generate_text
        raw = generate_text(
            task="script_analysis",
            system_prompt=system,
            user_message=f"Analyse and rewrite this SuperLiving ad script:\n\n{script}",
            provider=provider,
            temperature=0.3,
            max_tokens=8192,
        )
    else:
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=f"Analyse and rewrite this SuperLiving ad script:\n\n{script}",
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.3,
            ),
        )
        if response is None or response.text is None:
            return "", script  # Fail gracefully — return empty brief, original script
        raw = response.text.strip()

    if not raw:
        return "", script

    # Split on the IMPROVED SCRIPT delimiter
    delimiter = "IMPROVED SCRIPT\n==============="
    if delimiter in raw:
        parts = raw.split(delimiter, 1)
        production_brief = parts[0].strip()
        improved_script = parts[1].strip()
    else:
        # Fallback: whole response is the brief, return original script unchanged
        production_brief = raw
        improved_script = script

    return production_brief, improved_script


def build_clip_prompts(
    client,
    script: str,
    extra_prompt: str,
    extra_image_parts: list,
    character_sheet: str,
    photo_analyses: dict,
    aspect_ratio: str,
    num_clips: int,
    language_note: bool,
    has_photos: bool = False,
    production_brief: str = "",
    provider: str = None,
) -> list:
    ratio_map = {
        "9:16 (Reels / Shorts)": "9:16 vertical portrait",
        "16:9 (YouTube / Landscape)": "16:9 horizontal landscape",
        "9:16": "9:16 vertical portrait",
        "16:9": "16:9 horizontal landscape",
    }
    ar = ratio_map.get(aspect_ratio, "9:16 vertical portrait")
    extra_section = f"\nPRODUCTION NOTES: {extra_prompt}" if extra_prompt.strip() else ""
    language_note_line = (
        "\nNOTE: The original script may contain English or Hinglish — "
        "translate ALL spoken dialogue to Devanagari Hindi regardless."
    ) if language_note else ""

    # ── Character consistency block ───────────────────────────────────────────
    if has_photos and photo_analyses:
        outfit_block = "\n".join(
            f"  [{name}] LOCKED OUTFIT: {data['outfit']}"
            for name, data in photo_analyses.items()
            if data.get("outfit")
        )
        appearance_block = "\n".join(
            f"  [{name}] LOCKED APPEARANCE: {data['appearance']}"
            for name, data in photo_analyses.items()
            if data.get("appearance")
        )
        char_block = f"""━━━ LOCKED OUTFITS — copy verbatim into every clip, no paraphrasing ━━━
{outfit_block}

━━━ LOCKED APPEARANCE — copy verbatim into every clip, no paraphrasing ━━━
{appearance_block}

HOW THE I2V CHAIN WORKS (read carefully):
- Clip 1: original character reference photo → I2V frame 0. Text appearance anchors the face.
- Clips 2+: EXACT LAST FRAME of previous clip → I2V frame 0. Text appearance reinforces identity.
- Result: pixel-perfect match-cut continuity. Never break this chain."""
        char_sheet_injection = ""
    else:
        char_block = """CHARACTER SHEET LOCK:
Copy the locked character sheet verbatim into every clip's OUTFIT & APPEARANCE block.
Clips 2+ use the last frame of the previous clip as I2V — text still anchors identity."""
        char_sheet_injection = f"\n\nLOCKED CHARACTER SHEET:\n{character_sheet}"

    system = build_clip_prompts_system(
        num_clips=num_clips,
        ar=ar,
        char_block=char_block,
        language_note_line=language_note_line,
    )

    user_text = (
        f"SUPERLIVING AD SCRIPT:\n{script}"
        f"{char_sheet_injection}"
        f"{extra_section}"
        + (f"\n\nPRODUCTION BRIEF (from script analyst — follow these director notes precisely):\n{production_brief}"
           if production_brief else "")
        + f"\n\nGenerate exactly {num_clips} clip prompts as JSON now."
    )

    contents = [types.Part.from_text(text=user_text)]
    if extra_image_parts:
        contents.append(types.Part.from_text(
            text=f"\n\nREFERENCE IMAGES ({len(extra_image_parts)}): "
                 f"Match the visual tone, setting, and mood shown."
        ))
        contents.extend(extra_image_parts)

    if provider is not None:
        # Route through ai_router (Anthropic or Gemini)
        from .ai_router import generate_text
        # Flatten contents to text only (image parts not supported via router)
        raw = generate_text(
            task="clip_prompt_build",
            system_prompt=system,
            user_message=user_text,
            provider=provider,
            temperature=0.15,
        )
    else:
        # Default: Gemini with image parts support
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.15,
            ),
        )
        if response is None or response.text is None:
            raise RuntimeError("Gemini returned empty response when building clip prompts")
        raw = response.text.strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    data = json.loads(raw)
    clips = data["clips"]

    # ── Brightness boost for last 2 clips ─────────────────────────────────────
    # Veo's I2V chain causes progressive brightness decay by clips 4–6.
    # Even with "IDENTICAL to clip 1" in every clip, the model inherits the
    # darker I2V input frame and compounds the darkness. For the last 2 clips
    # we override with an explicit OVEREXPOSURE instruction so Veo fights back.
    STANDARD_ANCHOR = (
        "Exposure: same bright, well-lit level as clip 1. Face fully illuminated, "
        "no dimming, no shadow creep. Overall brightness IDENTICAL to clip 1. "
        "Camera exposure LOCKED."
    )
    BOOST_ANCHOR = (
        "⚡ BRIGHTNESS OVERRIDE (I2V drift compensation): Render this clip "
        "15–20% BRIGHTER than clip 1 to offset cumulative I2V brightness decay. "
        "Face must be OVER-LIT — deliberately elevated exposure, never dim. "
        "Ignore the darker starting I2V frame; boost exposure aggressively. "
        "Overall brightness visibly HIGHER than clip 1. Camera exposure BOOSTED."
    )
    if len(clips) >= 2:
        for idx in range(max(0, len(clips) - 2), len(clips)):
            prompt_val = clips[idx].get("prompt", "")
            if isinstance(prompt_val, str):
                clips[idx]["prompt"] = prompt_val.replace(STANDARD_ANCHOR, BOOST_ANCHOR)

    return clips

# CONTINUING FROM (Gemini Vision)
def build_continuing_from(
    gemini_client,
    frames: list,
    clip_num: int,
    next_scene_summary: str,
) -> str:
    """
    Send the last N frames to Gemini Vision and ask it to write a precise
    CONTINUING FROM: description based on what *actually rendered* in the video.
    """
    contents = []

    # Add all frames as image parts
    for i, frame_bytes in enumerate(frames):
        contents.append(
            types.Part.from_bytes(data=frame_bytes, mime_type="image/jpeg")
        )

    contents.append(types.Part.from_text(
        text=build_continuing_from_prompt(len(frames), next_scene_summary)
    ))

    response = gemini_client.models.generate_content(
        model="gemini-2.0-flash",
        contents=contents,
    )
    # Safely handle None response
    if response is None or response.text is None:
        return "CONTINUING FROM: [previous frame — details unavailable]"
    return response.text.strip()



# VIDEO GENERATION (Veo API calls)
def generate_clip_from_image(
    video_client, model: str, prompt: str, ar: str,
    clip_num: int, total: int,
    image_bytes: bytes, image_mime: str,
):
    """
    Generate clip 1: standard I2V using character reference photo.
    """
    config = types.GenerateVideosConfig(
        aspect_ratio=ar,
        number_of_videos=1,
        resolution="1080p",
        enhance_prompt=True,
    )
    operation = video_client.models.generate_videos(
        model=model,
        prompt=prompt,
        image=types.Image(image_bytes=image_bytes, mime_type=image_mime),
        config=config,
    )
    operation = poll_operation(video_client, operation, f"Rendering clip {clip_num}/{total}")
    return operation


def generate_clip_with_frame_context(
    video_client, gemini_client,
    model: str, base_prompt: str, ar: str,
    clip_num: int, total: int,
    prev_video_path: str,
    next_scene_summary: str,
    n_frames: int = 15,
):
    """
    Generate clips 2+:
      1. Extract last N frames of previous clip for Gemini analysis
      2. Send to Gemini → get accurate CONTINUING FROM: description (includes face analysis)
      3. Replace the prompt's CONTINUING FROM: with the Gemini-generated one
      4. Extract the single absolute last frame as I2V starting image for Veo
    """
    # Import here to avoid circular dependency
    from . import video_engine

    logger.info(f"  🎞️ Extracting last {n_frames} frames from clip {clip_num - 1} for analysis...")
    frames = video_engine.extract_last_n_frames(prev_video_path, n=n_frames)
    try:
        first_frame = video_engine.extract_frame_at(prev_video_path, t=0.5)
        # Prepend first frame so Gemini sees full start→end arc
        frames = [first_frame] + frames
    except Exception:
        pass  # non-fatal
    logger.info(f"  ✅ {len(frames)} frames extracted for Gemini analysis")

    logger.info(f"  🧠 Gemini analysing frames → generating CONTINUING FROM...")
    continuing_from = build_continuing_from(
        gemini_client, frames, clip_num, next_scene_summary
    )
    # ── Sanitize the Gemini-generated CONTINUING FROM for Veo-blocked terms ──
    # Gemini describes what it SEES in the frames — it may use appearance terms
    # like "clear skin", "healthy glow" that trigger Veo's content policy.
    # The main sanitizer skips CONTINUING FROM blocks, so we do a targeted cleanup here.
    _CF_REPLACEMENTS = [
        # English appearance terms
        ("clear skin", "natural complexion"), ("healthy skin", "natural complexion"),
        ("glowing skin", "natural complexion"), ("healthy glow", "natural warmth"),
        ("skin glow", "natural warmth"), ("skin is clear", "complexion is natural"),
        ("skin looks clear", "complexion looks natural"),
        ("skin looks healthy", "complexion looks natural"),
        ("skin looks better", "looks natural"), ("clearer skin", "natural complexion"),
        ("acne marks", "facial features"), ("pimple marks", "facial features"),
        ("dark spots", "facial features"), ("blemishes", "facial features"),
        ("oily skin", "natural skin texture"), ("dry skin", "natural skin texture"),
        # Hindi appearance terms
        ("साफ त्वचा", "प्राकृतिक रंगत"), ("स्वस्थ त्वचा", "प्राकृतिक रंगत"),
        ("चमकदार त्वचा", "प्राकृतिक रंगत"), ("त्वचा में सुधार", "प्राकृतिक रंगत"),
        ("मुंहासे", "चेहरे की बनावट"), ("दाग", "चेहरे की बनावट"),
        ("धब्बे", "चेहरे की बनावट"),
    ]
    for old, new in _CF_REPLACEMENTS:
        if old in continuing_from.lower():
            continuing_from = re.sub(re.escape(old), new, continuing_from, flags=re.IGNORECASE)
    logger.info(f"  📋 Auto-generated CONTINUING FROM for clip {clip_num}")

    # ── Combo: merge vision-verified CONTINUING FROM with scripted one ──────
    lines = base_prompt.split("\n")
    scripted_cf_lines = []
    other_lines = []
    in_cf = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("CONTINUING FROM:"):
            in_cf = True
            scripted_cf_lines.append(line)
        elif in_cf and stripped == "":
            in_cf = False
        elif in_cf:
            scripted_cf_lines.append(line)
        else:
            other_lines.append(line)

    scripted_cf = "\n".join(scripted_cf_lines).strip()

    if scripted_cf:
        merged_cf = (
            continuing_from
            + "\n\nSCRIPTED INTENT (planned narrative — reconcile with actual frame state above, "
            + "do NOT contradict the vision-verified CONTINUING FROM):\n"
            + scripted_cf.replace("CONTINUING FROM:", "SCRIPTED CONTINUING FROM:", 1)
        )
    else:
        merged_cf = continuing_from

    # ── Inject BACKGROUND FREEZE line into LOCATION block if missing ─────────
    FREEZE_LINE = "पृष्ठभूमि पूरी तरह स्थिर और अपरिवर्तित रहती है — कोई नई वस्तु नहीं आएगी, कोई वस्तु गायब नहीं होगी, रंग नहीं बदलेगा।"
    other_text = "\n".join(other_lines)
    if FREEZE_LINE not in other_text:
        location_injected = False
        new_other_lines = []
        for line in other_lines:
            new_other_lines.append(line)
            if line.strip().startswith("LOCATION:") and not location_injected:
                new_other_lines.append(FREEZE_LINE)
                location_injected = True
        if not location_injected:
            final_lines = []
            for line in new_other_lines:
                if line.strip().startswith("ACTION:") and not location_injected:
                    final_lines.append(f"LOCATION: {FREEZE_LINE}")
                    location_injected = True
                final_lines.append(line)
            new_other_lines = final_lines
        other_lines = new_other_lines

    # ── Inject FACE LOCK STATEMENT if missing ────────────────────────────────
    FACE_LOCK_LINE = "⚠️ चेहरा पूरी तरह स्थिर और क्लिप 1 के समान रहेगा — चेहरे की बनावट, त्वचा का रंग, आँखें, होंठ, बाल — कोई परिवर्तन नहीं।"
    if FACE_LOCK_LINE not in "\n".join(other_lines):
        # Inject after OUTFIT & APPEARANCE block
        new_other_lines = []
        injected = False
        for line in other_lines:
            new_other_lines.append(line)
            if (line.strip().startswith("OUTFIT") or line.strip().startswith("APPEARANCE")) and not injected:
                new_other_lines.append(FACE_LOCK_LINE)
                injected = True
        if not injected:
            new_other_lines.insert(0, FACE_LOCK_LINE)
        other_lines = new_other_lines

    updated_prompt = merged_cf + "\n\n" + "\n".join(other_lines).lstrip()

    # ── Extract single last frame → I2V starting image ───────────────────────
    logger.info(f"  🖼️ Extracting absolute last frame for I2V...")
    last_frame_bytes = video_engine.extract_last_frame(prev_video_path)
    logger.info(f"  ✅ Last frame extracted ({len(last_frame_bytes)//1024} KB) → I2V starting image")

    config = types.GenerateVideosConfig(
        aspect_ratio=ar,
        number_of_videos=1,
        resolution="1080p",
        enhance_prompt=True,
    )
    operation = video_client.models.generate_videos(
        model=model,
        prompt=updated_prompt,
        image=types.Image(image_bytes=last_frame_bytes, mime_type="image/jpeg"),
        config=config,
    )
    operation = poll_operation(
        video_client, operation,
        f"Rendering clip {clip_num}/{total} (last-frame I2V)"
    )
    return operation, updated_prompt


def generate_clip_text_only(
    video_client, model: str, prompt: str, ar: str,
    clip_num: int, total: int,
):
    """Fallback: generate with text prompt only."""
    config = types.GenerateVideosConfig(
        aspect_ratio=ar,
        number_of_videos=1,
        resolution="1080p",
        enhance_prompt=True,
    )
    operation = video_client.models.generate_videos(model=model, prompt=prompt, config=config)
    operation = poll_operation(video_client, operation, f"Rendering clip {clip_num}/{total}")
    return operation



# EXTRACT / DOWNLOAD

def extract_generated_video(operation, clip_num: int):
    """
    Pull the generated video object from a completed operation.
    Raises typed exceptions for RAI blocks so the caller can handle them.
    """
    logger.debug(f"🔍 Debug — Clip {clip_num} raw response: {str(operation)[:3000]}")

    # Check for RAI filter first
    for attr in ("response", "result"):
        obj = getattr(operation, attr, None)
        if obj is None:
            continue
        filtered_count   = getattr(obj, "rai_media_filtered_count",   0) or 0
        filtered_reasons = getattr(obj, "rai_media_filtered_reasons", []) or []
        if filtered_count > 0 or filtered_reasons:
            reasons_str = " | ".join(str(r) for r in filtered_reasons)
            if "celebrity" in reasons_str.lower():
                logger.warning(
                    f"🚫 Clip {clip_num}: I2V image flagged as celebrity likeness. "
                    f"Reason: {reasons_str}"
                )
                raise RaiCelebrityError(reasons_str)
            else:
                logger.warning(
                    f"🚫 Clip {clip_num}: RAI content filter triggered. "
                    f"Reason: {reasons_str}"
                )
                raise RaiContentError(reasons_str)

    generated = None
    if hasattr(operation, "response") and operation.response:
        generated = getattr(operation.response, "generated_videos", None)
    if not generated and hasattr(operation, "result") and operation.result:
        generated = getattr(operation.result, "generated_videos", None)

    if not generated:
        logger.error(
            f"Clip {clip_num} returned empty — likely a content policy block."
        )
        return None

    return generated[0].video


def download_video(uri: str, api_key: str) -> bytes:
    CHUNK = 1024 * 1024
    req = urllib.request.Request(uri, headers={"x-goog-api-key": api_key})
    buf = bytearray()
    with urllib.request.urlopen(req, timeout=300) as resp:
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            buf.extend(chunk)
    return bytes(buf)



# PROMPT SANITIZATION


def sanitize_prompt_for_veo(client, prompt: str, clip_num: int, provider: str = None) -> str:
    """
    Run every prompt through Gemini (or Claude) BEFORE sending to Veo.
    Strips anything that triggers Veo's content policy silently.
    """
    system = SANITIZE_VEO_SYSTEM

    if provider is not None:
        from .ai_router import generate_text
        sanitized = generate_text(
            task="sanitization",
            system_prompt=system,
            user_message=f"Sanitize this Veo prompt for clip {clip_num}:\n\n{prompt}",
            provider=provider,
            temperature=0.3,
        )
        return sanitized if sanitized else prompt

    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=f"Sanitize this Veo prompt for clip {clip_num}:\n\n{prompt}",
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.3,
        ),
    )
    # Safely handle None response
    if response is None or response.text is None:
        return prompt  # Return original if sanitizer fails
    sanitized = response.text.strip()
    if sanitized.startswith("```"):
        lines = sanitized.split("\n")
        sanitized = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
    return sanitized.strip() if sanitized else prompt


def hyphenate_dialogue_acronyms(prompt: str) -> str:
    """
    Deterministically hyphenate ALL-CAPS acronyms (2–6 chars) inside the DIALOGUE
    section of a Veo prompt — guaranteed, no LLM involved.

    Why this works: Veo's TTS pronounces "PCOS" as a single mumbled syllable but
    pronounces "P-C-O-S" as four distinct letters.  LLM instructions are unreliable
    (Gemini may not follow them, and the sanitizer may strip the hyphenated form).
    This function runs AFTER every LLM call, so the final text Veo receives is always
    correct regardless of what the LLMs produced.

    Handles both prompt formats:
      • JSON-style:  "DIALOGUE": "...PCOS..."
      • Plain-text:  DIALOGUE:\n...PCOS...
    """
    import re as _re

    _ACRONYM = _re.compile(r'\b([A-Z]{2,6})\b')

    def _hyphenate(text: str) -> str:
        return _ACRONYM.sub(lambda m: '-'.join(m.group(1)), text)

    # ── JSON-format prompt ("DIALOGUE": "...") ───────────────────────────────
    _JSON_DIALOGUE = _re.compile(
        r'("DIALOGUE"\s*:\s*")((?:[^"\\]|\\.)*?)(")',
        _re.IGNORECASE,
    )
    if _JSON_DIALOGUE.search(prompt):
        return _JSON_DIALOGUE.sub(
            lambda m: m.group(1) + _hyphenate(m.group(2)) + m.group(3),
            prompt,
        )

    # ── Plain-text format (DIALOGUE:\n...) ───────────────────────────────────
    lines = prompt.split('\n')
    in_dialogue = False
    result = []
    for line in lines:
        stripped = line.strip()
        if _re.match(r'^DIALOGUE\s*:', stripped, _re.IGNORECASE):
            in_dialogue = True
            result.append(line)
            continue
        # Any new ALL-CAPS-ish section header ends the DIALOGUE block
        if in_dialogue and _re.match(r'^[A-Z][A-Z\s&_()\-]{1,40}:', stripped):
            in_dialogue = False
        if in_dialogue:
            line = _hyphenate(line)
        result.append(line)
    return '\n'.join(result)


def rephrase_blocked_prompt(client, original_prompt: str, attempt: int) -> str:
    """Post-block rephrasing — more aggressive than sanitize, used after a Veo rejection."""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=rephrase_blocked_prompt_contents(attempt, original_prompt),
        )
        # Safely handle None response
        if response is None or response.text is None:
            logger.warning("⚠️ Rephrase returned empty — using original prompt")
            return original_prompt
        return response.text.strip()
    except Exception as e:
        logger.warning(f"⚠️ Rephrase failed ({e}) — using original prompt")
        return original_prompt
"""
AI Engine for SuperLiving ad generation.
"""

import json
import logging
import time
import urllib.request

from google import genai
from google.genai import types

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
            types.Part.from_text(text=(
                f"Analyse this person's photo and return a JSON object with exactly two keys.\n\n"
                f"Key 1 — 'appearance': describe ONLY the static physical baseline (face and body).\n"
                f"Include: skin tone (exact), face shape, eye shape+color+spacing, brow shape, "
                f"nose shape, lip fullness, jawline, distinctive marks, hair (color/texture/length/style), "
                f"age range, build. \n"
                f"CRITICAL: DO NOT include any temporary emotions, expressions, or time-based changes "
                f"(e.g., never say 'at first', 'becomes', 'looks sad'). Keep it 100% physically neutral.\n"
                f"One dense paragraph starting with '{name} is a [age]...'.\n\n"
                f"Key 2 — 'outfit': describe ONLY what they are wearing.\n"
                f"Every garment, color, fabric, pattern, fit. Be exact — this is locked forever.\n"
                f"One sentence starting with 'Wearing...'.\n\n"
                f"Return ONLY valid JSON: {{\n  \"appearance\": \"...\",\n  \"outfit\": \"...\"\n}}\n"
                f"No markdown, no preamble."
            )),
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


def build_character_sheet(client, script: str) -> str:
    """Used only when no reference photos are provided."""
    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=(
            f"Read this ad script. For every named or described character, create a "
            f"LOCKED visual profile (identical across all clips).\n\n"
            f"Include: exact age, skin tone, face shape, eyes, brows, nose, lips, jawline, "
            f"hair (length/color/texture/style), build, "
            f"LOCKED OUTFIT (exact garment/color/fabric — never changes), "
            f"accessories, signature expression.\n\n"
            f"CRITICAL RULE: Describe the neutral, static physical baseline ONLY. "
            f"DO NOT include time-based changes or temporary emotions (e.g., never write "
            f"'her hair gets smoother later' or 'her expression changes').\n\n"
            f"FORMAT:\nCHARACTER: [Name/Role]\n"
            f"OUTFIT: [one sentence, exact garment]\n"
            f"APPEARANCE: [all other details, one dense paragraph]\n\n"
            f"AD SCRIPT:\n{script}"
        ),
    )
    # Safely handle None response
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
def build_clip_prompts(
    client,
    script: str,
    extra_prompt: str,
    extra_image_parts: list,        # list of types.Part (images from additional instructions)
    character_sheet: str,
    photo_analyses: dict,           # {name: {"appearance": str, "outfit": str}}
    aspect_ratio: str,
    num_clips: int,
    language_note: bool,
    has_photos: bool = False,
) -> list:
    ratio_map = {
        "9:16 (Reels / Shorts)": "9:16 vertical portrait",
        "16:9 (YouTube / Landscape)": "16:9 horizontal landscape",
    }
    ar = ratio_map.get(aspect_ratio, "9:16 vertical portrait")
    extra_section = f"\nPRODUCTION NOTES: {extra_prompt}" if extra_prompt.strip() else ""

    if has_photos and photo_analyses:
        outfit_block = "\n".join(
            f"  [{name}] LOCKED OUTFIT: {data['outfit']}"
            for name, data in photo_analyses.items()
            if data.get("outfit")
        )
        appearance_block = "\n".join(
            f"  [{name}] APPEARANCE: {data['appearance']}"
            for name, data in photo_analyses.items()
            if data.get("appearance")
        )
        char_consistency_rule = f"""CHARACTER CONSISTENCY — DUAL LOCK (image + text):

HOW CONTINUITY WORKS IN THIS PIPELINE:
- Clip 1: original character photo sent as I2V starting image.
- Clips 2+: the EXACT LAST FRAME of the previous clip is sent as I2V starting image.
  This creates a pixel-perfect match-cut — the new clip begins exactly where the
  previous one ended. Combined with a Gemini Vision CONTINUING FROM description of
  the actual rendered frames, Veo generates a seamless continuation with no visual
  discontinuity. NEVER use a solo portrait photo for clips that show multiple characters.
- Every clip: locked outfit + appearance injected as text to anchor frames throughout.

━━━ LOCKED OUTFITS (HIGHEST PRIORITY — verbatim in every prompt) ━━━
{outfit_block}

These outfits NEVER change across any clip, any scene, ever.

━━━ LOCKED APPEARANCE (copy verbatim for every character present in the clip) ━━━
{appearance_block}

RULES:
1. First line of every prompt: "[Name] is wearing [locked outfit] and [locked appearance]."
2. Do NOT paraphrase, shorten, or vary either description.
3. The outfit line must appear even if the character barely appears."""
        char_sheet_injection = ""

    else:
        char_consistency_rule = """CHARACTER CONSISTENCY — MOST CRITICAL RULE:
Copy-paste BOTH the outfit line AND appearance paragraph verbatim for every character present.
Clips 2+ use the exact last frame of the previous clip as I2V starting image — the text
anchors must still be present in every prompt to reinforce consistency throughout."""
        char_sheet_injection = f"\n\nLOCKED CHARACTER SHEET:\n{character_sheet}"

    system = f"""You are an expert AI video director creating prompts for Google Veo 3.1.

TASK: Split the given SuperLiving ad script into exactly {num_clips} sequential 8-second clip prompts.
ALL CLIP PROMPTS MUST BE WRITTEN IN DEVANAGARI HINDI.

{char_consistency_rule}

THE "RULE OF ONE ACTION" & CAMERA GEOMETRY (CRITICAL FOR VEO):
Diffusion models 'melt' if overloaded. You MUST follow these isolation rules:
- ACTION ISOLATION: Never overload an 8-second clip. If a character changes emotion (e.g., sad to happy), their body MUST remain absolutely still (write: "शरीर बिल्कुल स्थिर रहता है, हाथ नीचे ही रहेंगे"). 
- If a character does a physical action (dropping products, lifting phone), their emotion must already be established.
- CAMERA LOCK: Whenever a character moves their hands or body, you MUST use "(STATIC SHOT) / कैमरा बिल्कुल स्थिर रहता है". Do NOT zoom or pan while a character is moving.
- LOCATION LOCK — BACKGROUND FREEZE (MOST CRITICAL ANTI-HALLUCINATION RULE):
  STEP 1 — Before writing any clip, write one LOCKED BACKGROUND sentence of at least 50 words.
  Describe like a set decorator's bible: exact wall color/texture, every visible shelf item (position: left/center/right, color, shape, count), floor/counter material, light source direction and color temperature, any furniture edges visible.
  EXAMPLE OF CORRECT LOCKED BACKGROUND:
  "LOCKED BACKGROUND: मैट ग्रे दीवार, पीछे तीन सफ़ेद शेल्फ — बाईं शेल्फ पर दो सफ़ेद ट्यूब और एक भूरी बोतल, बीच की शेल्फ पर तीन सफ़ेद बोतल, दाईं शेल्फ पर दो क्रीम रंग की ट्यूब — नीचे सफ़ेद मार्बल काउंटर, बाईं तरफ से नरम सफ़ेद रोशनी, दाईं दीवार सादी ग्रे।"
  STEP 2 — Copy this EXACT sentence VERBATIM into the LOCATION block of EVERY SINGLE clip. Not paraphrased. Not shortened. Word for word.
  STEP 3 — End every clip's LOCATION block with this mandatory freeze line (copy verbatim):
  "पृष्ठभूमि पूरी तरह स्थिर और अपरिवर्तित रहती है — कोई नई वस्तु नहीं आएगी, कोई वस्तु गायब नहीं होगी, रंग नहीं बदलेगा।"
  VIOLATION: If any clip has a different LOCATION description than clip 1, that is a fatal error.

UI & HALLUCINATION GUARDRAILS:
- THE PHONE SCREEN TRAP: Veo cannot render a second human face inside a phone screen. If a phone is shown, you MUST state: "फोन की स्क्रीन काली है" (The phone screen is black). NEVER describe an app UI or a video call.

DIALOGUE LENGTH — THE LIP-SYNC 'GOLDILOCKS ZONE':
- STRICT LIMIT: Exactly 15 to 19 Hindi words of spoken dialogue per clip. 
- Less than 15 words causes the AI to speak in slow-motion.
- More than 20 words causes rushed, chipmunk-speed speech and breaks lip-sync.
- VOICE CONSISTENCY: Keep emotion tags inside dialogue brackets subtle and consistent. Always start the bracket with '(बातचीत के लहजे में...)' so the AI voice engine does not fluctuate its pitch between clips.
- Balance the script perfectly to hit 15-19 words per 8-second clip. Split long sentences across clips seamlessly.
- Format: चरित्र: "संवाद"

CONTINUITY RULES:
- Every prompt except clip 1 MUST begin with a CONTINUING FROM: block describing the exact last frame of the previous clip.
- The CONTINUING FROM block MUST include a full background inventory: list every object visible behind the character (shelf items by position, wall color, counter surface, light direction). This prevents Veo from hallucinating new background objects.
- End every prompt with: "LAST FRAME: [exact position, expression, camera, framing, AND full background object list]"

CLIP PROMPT STRUCTURE:
1. CONTINUING FROM: [For clips 2+]
2. OUTFIT & APPEARANCE: [Locked, verbatim]
3. LOCATION: [Locked, no panning]
4. ACTION: [Emotion + strictly isolated body movement]
5. DIALOGUE: [Strictly 15-19 words]
6. AUDIO: [BGM consistent]
7. CAMERA: [Describe angle]. MUST include: "Ultra-sharp focus, 8k resolution, highly detailed."
8. LIGHTING: [Describe lighting]. MUST include: "Cinematic contrast, photorealistic skin texture, extremely crisp."
9. LAST FRAME: [Anchor for next clip]

AUDIO-VISUAL SYNC:
Add to every prompt: "Audio-visual sync: match lip movements precisely to spoken dialogue."

VISUAL FORMAT PROHIBITIONS:
Add to every prompt: "No cinematic letterbox bars. No black bars. Full {ar} frame edge to edge. No burned-in subtitles. No text overlays. No lower thirds. No captions. No watermarks. No on-screen app UI. If showing phone, show dark screen only."

{"Dialogue: note tone e.g. 'warmly, looking at camera'" if language_note else ""}

OUTPUT: valid JSON only:
{{
  "clips": [
    {{"clip": 1, "scene_summary": "...", "last_frame": "...", "prompt": "..."}},
    ... (exactly {num_clips} items)
  ]
}}"""

    # Build the contents list — text first, then any reference images
    user_text = (
        f"SUPERLIVING AD SCRIPT:\n{script}"
        f"{char_sheet_injection}"
        f"{extra_section}"
        f"\n\nGenerate exactly {num_clips} clip prompts as JSON now."
    )

    contents = [types.Part.from_text(text=user_text)]

    # Append reference images from additional instructions
    if extra_image_parts:
        contents.append(types.Part.from_text(
            text=f"\n\nREFERENCE IMAGES ({len(extra_image_parts)} provided): "
                 f"Use these as visual context for setting, mood, style, and aesthetics "
                 f"when writing the scene prompts. Match the look and feel shown."
        ))
        contents.extend(extra_image_parts)

    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.5,
        ),
    )

    # Safely handle None response
    if response is None or response.text is None:
        raise RuntimeError("Gemini returned empty response when building clip prompts")
    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    data = json.loads(raw)
    return data["clips"]



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

    WHY THIS BEATS A MANUALLY-WRITTEN CONTINUING FROM:
    The manually-written version describes what the prompt *said* should happen.
    Gemini watching real frames describes what Veo *actually rendered* — the true
    final position, expression, lighting, camera angle, and emotional state.
    This gives the next clip a factually accurate visual anchor.
    """
    contents = []

    # Add all frames as image parts
    for i, frame_bytes in enumerate(frames):
        contents.append(
            types.Part.from_bytes(data=frame_bytes, mime_type="image/jpeg")
        )

    contents.append(types.Part.from_text(text=(
        f"These {len(frames)} images are the last frames of a video clip "
        f"(ordered earliest to latest, sampled from the final 2 seconds).\n\n"
        f"The NEXT clip that will be generated is: '{next_scene_summary}'\n\n"
        f"Write a precise CONTINUING FROM: description for the Veo prompt of the next clip.\n"
        f"Describe EXACTLY what is visible in the final frame:\n"
        f"- Character: exact position (standing/sitting/facing which direction), "
        f"  expression (precise — e.g. 'mid-smile, eyes slightly wet'), "
        f"  hand/body position, what they just finished saying or doing\n"
        f"- BACKGROUND INVENTORY (most critical — be exhaustive, not vague): "
        f"  List EVERY visible object behind the character one by one. "
        f"  For shelves: count the items, state their exact color and position (left/center/right). "
        f"  For walls: exact color and texture. "
        f"  For counters/floors: material and color. "
        f"  Do NOT write 'shelves with products' — write 'left shelf: two white tubes and one brown bottle, "
        f"  center shelf: three white bottles, right shelf: two cream tubes'. "
        f"  This inventory is what prevents the next clip from hallucinating new objects.\n"
        f"- Camera: angle (eye-level/low/high), distance (close-up/medium/wide), "
        f"  any movement that was happening (pan/tilt/static)\n"
        f"- Lighting: quality (soft/harsh), direction (from left/right/behind), "
        f"  color temperature (warm/cool/golden)\n"
        f"- Audio state: was anyone speaking, what was the BGM doing\n"
        f"- Emotional momentum: what feeling is in the air as this clip ends\n\n"
        f"Format: start directly with 'CONTINUING FROM:' — no preamble.\n"
        f"Keep it under 180 words. Be exhaustively specific about the background. Be factual, not poetic."
    )))

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
    config = types.GenerateVideosConfig(aspect_ratio=ar, number_of_videos=1)
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
    n_frames: int = 10,
):
    """
    Generate clips 2+:
      1. Extract last N frames of previous clip for Gemini analysis
      2. Send to Gemini → get accurate CONTINUING FROM: description
      3. Replace the prompt's CONTINUING FROM: with the Gemini-generated one
      4. Extract the single absolute last frame as I2V starting image for Veo

    This gives Veo two strong continuity signals:
      - Visual: the exact last frame as I2V anchor (pixel-perfect match-cut)
      - Textual: a Gemini-verified CONTINUING FROM: based on real rendered frames
    """
    # Import here to avoid circular dependency
    from . import video_engine

    logger.info(f"  🎞️ Extracting last {n_frames} frames from clip {clip_num - 1} for analysis...")
    frames = video_engine.extract_last_n_frames(prev_video_path, n=n_frames)
    logger.info(f"  ✅ {len(frames)} frames extracted for Gemini analysis")

    logger.info(f"  🧠 Gemini analysing frames → generating CONTINUING FROM...")
    continuing_from = build_continuing_from(
        gemini_client, frames, clip_num, next_scene_summary
    )
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
            + "\n\nSCRIPTED INTENT (planned narrative — use alongside actual frame state above):\n"
            + scripted_cf.replace("CONTINUING FROM:", "SCRIPTED CONTINUING FROM:", 1)
        )
    else:
        merged_cf = continuing_from

    updated_prompt = merged_cf + "\n\n" + "\n".join(other_lines).lstrip()

    # ── Extract single last frame → I2V starting image ───────────────────────
    logger.info(f"  🖼️ Extracting absolute last frame for I2V...")
    last_frame_bytes = video_engine.extract_last_frame(prev_video_path)
    logger.info(f"  ✅ Last frame extracted ({len(last_frame_bytes)//1024} KB) → I2V starting image")

    config = types.GenerateVideosConfig(aspect_ratio=ar, number_of_videos=1)
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
    config = types.GenerateVideosConfig(aspect_ratio=ar, number_of_videos=1)
    operation = video_client.models.generate_videos(model=model, prompt=prompt, config=config)
    operation = poll_operation(video_client, operation, f"Rendering clip {clip_num}/{total}")
    return operation



# EXTRACT / DOWNLOAD

def extract_generated_video(operation, clip_num: int):
    """
    Pull the generated video object from a completed operation.
    Raises typed exceptions for RAI blocks so the caller can handle them:
      - RaiCelebrityError: I2V input image flagged as celebrity → retry without image
      - RaiContentError:   prompt blocked → trigger rephrase and retry
    """
    logger.debug(f"🔍 Debug — Clip {clip_num} raw response: {str(operation)[:3000]}")

    # Check for RAI filter first — these come back as done=True but with no video
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


def sanitize_prompt_for_veo(client, prompt: str, clip_num: int) -> str:
    """
    Run every prompt through Gemini BEFORE sending to Veo.
    Strips anything that triggers Veo's content policy silently.

    WHY PRE-SANITIZE:
    Veo blocks prompts silently (returns empty) for a wide range of triggers —
    not just explicit health claims, but indirect references, Hindi words that
    map to medical concepts, certain emotional framings, and more. Waiting for
    a block wastes a full 3-6 minute generation. Pre-sanitizing catches 95% of
    issues before they reach Veo.

    WHAT GETS REPLACED (not removed — replacement preserves narrative intent):
    - Health conditions → lifestyle / energy / confidence framings
    - Medicine / supplement references → "daily routine", "morning ritual"
    - Body weight / size references → movement, strength, vitality
    - Symptoms (fatigue, pain, ache) → "busy day", "active life", "needed rest"
    - Before/after framings → present-tense confidence
    - Hindi medical terms (दर्द, थकान, बीमारी, दवाई etc.) → emotional equivalents

    NEVER TOUCHED:
    - Outfit + appearance lines (character consistency)
    - CONTINUING FROM / LAST FRAME blocks (visual continuity)
    - Camera, lighting, location descriptions
    - No-letterbox / no-subtitle safety lines
    """
    system = """You are a Veo content policy expert. Sanitize video generation prompts so they NEVER get blocked by Google Veo.

Veo SILENTLY BLOCKS prompts containing ANY of these — even indirect references:

SKIN / SKINCARE (most common block for this app):
- Any mention of: skin type, skin condition, acne, pimples, marks, spots, blemishes,
  dark spots, pigmentation, oily skin, dry skin, clear skin, healthy skin, glowing skin,
  skin care routine, face wash, cream, serum, moisturizer, cleanser, toner
- Hindi: स्किन टाइप, त्वचा का प्रकार, मुंहासे, दाग, धब्बे, साफ त्वचा, स्वस्थ त्वचा,
  चमकदार त्वचा (when used as improvement), फेस वॉश, क्रीम, सीरम
- TRANSFORMATION LANGUAGE in appearance blocks: "त्वचा साफ और स्वस्थ दिखती है",
  "skin looks better now", "clearer skin", "त्वचा में सुधार" — these imply before/after

MEDICAL/HEALTH:
  disease, condition, symptom, diagnosis, treatment, cure, medicine, supplement,
  pill, BP, blood pressure, sugar, diabetes, thyroid, cholesterol, weight loss,
  fat loss, obesity, pain, ache, fatigue, weakness, inflammation, doctor, prescription
HINDI: दर्द, थकान, कमज़ोरी, बीमारी, दवाई, दवा, इलाज, डॉक्टर, वज़न, मोटापा,
  शुगर, बीपी, थायरॉइड, तकलीफ़

IMPROVEMENT CLAIMS (very commonly blocked):
- "बेहतर होगा / होगी" (will get better) → replace with "अच्छा लगेगा" (will feel good)
- "सब ठीक हो जाएगा" (everything will be fine) → "सब साफ दिखेगा" (everything will be clear)
- "सुधार होगा" (improvement will happen) → remove or replace with confidence framing
- ANY phrase implying a product/routine will FIX or IMPROVE a physical condition

BEFORE/AFTER: any physical transformation implication, body-state comparison,
  "2 mahine baad" framing that implies physical change (keep time references but
  remove physical transformation language)

SAFE REPLACEMENTS:
- स्किन टाइप समझ → अपनी दिनचर्या समझ (understand your routine)
- फिर सब बेहतर होगा → फिर सब आसान होगा (then everything will be easy)
- त्वचा साफ और स्वस्थ दिखती है → चेहरे पर एक नई ताज़गी है (a new freshness on his face)
- साफ त्वचा / clear skin → आत्मविश्वासी चेहरा (confident face)
- skin care routine → सुबह की आदत / morning habit
- फेस वॉश / face wash → मुँह धोना (washing face) — describe action not product
- थकान → व्यस्त दिन / busy day
- दर्द → तनाव / stress
- कमज़ोरी → नई ऊर्जा / new energy
- वज़न कम → आत्मविश्वास / confidence
- feels better → feels confident / energetic

ABSOLUTE RULES:
1. NEVER change: outfit descriptions, CONTINUING FROM blocks, LAST FRAME blocks,
   camera/lighting/location descriptions, no-letterbox/no-subtitle lines
2. FOR APPEARANCE BLOCKS: keep physical description (face shape, eyes, hair, build)
   but REMOVE any language about skin condition improvement or transformation
3. PRESERVE full prompt length — every removed phrase gets a safe replacement
4. Keep all Hindi — just swap blocked words/phrases
5. Keep character names and speaker-colon dialogue format
6. Output the sanitized prompt ONLY — no preamble, no explanation, no markdown"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
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


def rephrase_blocked_prompt(client, original_prompt: str, attempt: int) -> str:
    """Post-block rephrasing — more aggressive than sanitize, used after a Veo rejection."""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=(
                f"This Veo prompt was BLOCKED by safety policy. Aggressive rephrase attempt {attempt}.\n\n"
                f"BLOCKED TRIGGERS TO ELIMINATE:\n"
                f"- Skin: स्किन टाइप, त्वचा का प्रकार, साफ/स्वस्थ त्वचा, मुंहासे, दाग, चमकदार त्वचा\n"
                f"- Improvement claims: बेहतर होगा, सुधार होगा, सब ठीक होगा, will get better\n"
                f"- Skincare products: फेस वॉश, क्रीम, सीरम, moisturizer, face wash\n"
                f"- Health: थकान, दर्द, कमज़ोरी, बीमारी, दवाई, वज़न, BP, sugar, diabetes\n"
                f"- Before/after: transformation language, physical improvement comparisons\n\n"
                f"REPLACEMENTS:\n"
                f"- स्किन टाइप समझ → अपनी दिनचर्या समझ\n"
                f"- बेहतर होगा → आसान होगा / अच्छा लगेगा\n"
                f"- त्वचा साफ/स्वस्थ → चेहरे पर ताज़गी है\n"
                f"- साफ त्वचा → आत्मविश्वासी चेहरा\n"
                f"- फेस वॉश/क्रीम → describe the action (मुँह धोना) not the product\n"
                f"- थकान→व्यस्त दिन, दर्द→तनाव, कमज़ोरी→नई ऊर्जा, वज़न→आत्मविश्वास\n\n"
                f"MUST KEEP EXACTLY AS-IS:\n"
                f"- Outfit + physical appearance description (face shape, eyes, hair, build)\n"
                f"- CONTINUING FROM: block\n"
                f"- LAST FRAME: block\n"
                f"- Camera / lighting / location lines\n"
                f"- No-letterbox / no-subtitle lines\n\n"
                f"Output the rewritten prompt ONLY — no preamble, no explanation.\n\n"
                f"ORIGINAL BLOCKED PROMPT:\n{original_prompt}"
            ),
        )
        # Safely handle None response
        if response is None or response.text is None:
            logger.warning("⚠️ Rephrase returned empty — using original prompt")
            return original_prompt
        return response.text.strip()
    except Exception as e:
        logger.warning(f"⚠️ Rephrase failed ({e}) — using original prompt")
        return original_prompt
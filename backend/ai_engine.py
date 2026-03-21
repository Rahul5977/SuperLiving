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
                f"Include: skin tone (exact hex-like description), face shape, eye shape+color+spacing, brow shape, "
                f"nose shape, lip fullness+color, jawline, distinctive marks, hair (color/texture/length/style), "
                f"age range, build, ear shape, cheekbone prominence.\n"
                f"CRITICAL: DO NOT include any temporary emotions, expressions, or time-based changes "
                f"(e.g., never say 'at first', 'becomes', 'looks sad'). Keep it 100% physically neutral.\n"
                f"One dense paragraph starting with '{name} is a [age]...'.\n\n"
                f"Key 2 — 'outfit': describe ONLY what they are wearing.\n"
                f"Every garment, color, fabric, pattern, fit, neckline. Be exhaustively exact — this is locked forever.\n"
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
            f"Include: exact age, skin tone (hex-like), face shape, eyes (shape/color/size/spacing), "
            f"brows, nose, lips (fullness/color), jawline, cheekbones, "
            f"hair (length/color/texture/style), build, "
            f"LOCKED OUTFIT (exact garment/color/fabric — never changes), "
            f"accessories, any distinctive marks.\n\n"
            f"CRITICAL RULE: Describe the neutral, static physical baseline ONLY. "
            f"DO NOT include time-based changes or temporary emotions.\n\n"
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
            f"  [{name}] LOCKED APPEARANCE: {data['appearance']}"
            for name, data in photo_analyses.items()
            if data.get("appearance")
        )
        char_consistency_rule = f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHARACTER FACE LOCK — THIS IS THE MOST CRITICAL RULE IN THIS ENTIRE PROMPT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The viewer is watching a continuous ad. If the character's face changes even 1%, 
the viewer's trust breaks instantly. Face consistency = ad credibility.

HOW THE I2V CONTINUITY CHAIN WORKS:
- Clip 1: the character's reference photo is sent as the LITERAL first frame to Veo.
  The face in that photo IS the character. Veo must continue from that exact face.
- Clips 2+: the EXACT LAST FRAME of the previous clip is sent as the starting frame.
  This creates a pixel-perfect match-cut. Combined with the locked appearance text below,
  Veo has TWO anchors preventing any drift.

━━━ LOCKED OUTFITS — COPY VERBATIM INTO EVERY PROMPT, ZERO VARIATION ━━━
{outfit_block}

These outfit descriptions are immutable. Copy them word-for-word. NEVER shorten, rephrase, 
or vary. Not even one word different. The outfit must be identical in EVERY clip.

━━━ LOCKED APPEARANCE — COPY VERBATIM INTO EVERY PROMPT ━━━
{appearance_block}

FACE LOCK ENFORCEMENT RULES (VIOLATIONS = BROKEN AD):
1. Line 1 of EVERY prompt MUST be: "[Name] is wearing [locked outfit verbatim]. [locked appearance verbatim]."
2. NEVER write "similar to", "like in the previous clip", "as before" — always write the full text.
3. NEVER add ANY new physical descriptors not in the locked appearance block.
4. NEVER remove ANY physical descriptors from the locked appearance block.
5. Hair style, lip color, eye makeup, earrings, moles, scars — if in appearance block, copy exactly.
6. The character's face in clip 6 must be INDISTINGUISHABLE from clip 1. Same bone structure,
   same skin tone, same eyes, same lips — identical human being throughout."""
        char_sheet_injection = ""

    else:
        char_consistency_rule = """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHARACTER FACE LOCK — THIS IS THE MOST CRITICAL RULE IN THIS ENTIRE PROMPT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The viewer watches a continuous ad. A face change = broken immersion = failed ad.
Copy BOTH the outfit line AND the full appearance paragraph verbatim into EVERY single prompt.
Clips 2+ use the last frame of the previous clip as I2V starting image.
The text appearance anchor PLUS the visual I2V anchor together lock the face across all clips.
ANY abbreviation, paraphrase, or omission of the appearance block will cause face drift."""
        char_sheet_injection = f"\n\nLOCKED CHARACTER SHEET:\n{character_sheet}"

    system = f"""You are an expert AI video director creating prompts for Google Veo 3.1.

TASK: Split the given SuperLiving ad script into exactly {num_clips} sequential 8-second clip prompts.
ALL CLIP PROMPTS MUST BE WRITTEN IN DEVANAGARI HINDI.

{char_consistency_rule}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VIEWER ENGAGEMENT — THE AD MUST BE COMPELLING AND WATCHABLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The person watching this ad on a phone must be engaged within 2 seconds or they scroll away.
Design prompts so:
- The character always faces camera naturally, with relatable, warm energy
- Eye contact with camera feels personal and direct (like talking to a friend)
- Natural hand gestures that feel real, not stiff or robotic
- Warm, well-lit face — viewer must feel connection and trust
- Emotional arc: each clip advances from problem → recognition → curiosity → solution → confidence

THE "RULE OF ONE ACTION" & CAMERA GEOMETRY (CRITICAL FOR VEO):
Diffusion models 'melt' if overloaded. Follow these isolation rules:
- ACTION ISOLATION: Never overload an 8-second clip. If a character changes emotion (e.g., sad to happy),
  their body MUST remain absolutely still (write: "शरीर बिल्कुल स्थिर रहता है, हाथ नीचे ही रहेंगे"). 
- If a character does a physical action (dropping products, lifting phone), their emotion must already be established.
- CAMERA LOCK: Whenever a character moves their hands or body, you MUST use "(STATIC SHOT) / कैमरा बिल्कुल स्थिर रहता है".
  Do NOT zoom or pan while a character is moving.
- LOCATION LOCK — BACKGROUND FREEZE (MOST CRITICAL ANTI-HALLUCINATION RULE):
  STEP 1 — Before writing any clip, write one LOCKED BACKGROUND sentence of at least 50 words.
  Describe like a set decorator's bible: exact wall color/texture, every visible shelf item (position: left/center/right,
  color, shape, count), floor/counter material, light source direction and color temperature, any furniture edges visible.
  EXAMPLE OF CORRECT LOCKED BACKGROUND:
  "LOCKED BACKGROUND: मैट ग्रे दीवार, पीछे तीन सफ़ेद शेल्फ — बाईं शेल्फ पर दो सफ़ेद ट्यूब और एक भूरी बोतल,
  बीच की शेल्फ पर तीन सफ़ेद बोतल, दाईं शेल्फ पर दो क्रीम रंग की ट्यूब — नीचे सफ़ेद मार्बल काउंटर,
  बाईं तरफ से नरम सफ़ेद रोशनी, दाईं दीवार सादी ग्रे।"
  STEP 2 — Copy this EXACT sentence VERBATIM into the LOCATION block of EVERY SINGLE clip.
  STEP 3 — End every clip's LOCATION block with:
  "पृष्ठभूमि पूरी तरह स्थिर और अपरिवर्तित रहती है — कोई नई वस्तु नहीं आएगी, कोई वस्तु गायब नहीं होगी, रंग नहीं बदलेगा।"
  VIOLATION: If any clip has a different LOCATION description than clip 1, that is a fatal error.

UI & HALLUCINATION GUARDRAILS:
- THE PHONE SCREEN TRAP: Veo cannot render a second human face inside a phone screen.
  If a phone is shown, you MUST state: "फोन की स्क्रीन काली है". NEVER describe an app UI or a video call.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DIALOGUE — LIP-SYNC GOLDILOCKS ZONE (EXTREMELY IMPORTANT)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- STRICT LIMIT: Exactly 15 to 18 Hindi words of spoken dialogue per clip. COUNT EVERY WORD.
- Fewer than 15 words → AI speaks in slow-motion, looks unnatural
- More than 18 words → rushed chipmunk speech, broken lip-sync
- After writing dialogue, COUNT THE WORDS. If not 15-19, rewrite until it is.
- VOICE CONSISTENCY: Always start emotion tag with '(बातचीत के लहजे में...)' — this locks the AI voice tone
- Format: चरित्र: "(बातचीत के लहजे में, [emotion]) [dialogue]"
- Dialogue must flow naturally from clip to clip — no abrupt topic jumps

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONTINUITY RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Every prompt except clip 1 MUST begin with a CONTINUING FROM: block describing the exact last frame
  of the previous clip (verified from the LAST FRAME field of the previous clip).
- The CONTINUING FROM block MUST include: character position, expression, hand placement,
  AND full background inventory (every shelf object by position).
- End every prompt with: "LAST FRAME: [exact position, expression, hand placement, camera angle/distance, full background object list]"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CLIP PROMPT STRUCTURE — ALL SECTIONS MANDATORY IN EVERY CLIP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. CONTINUING FROM: [Clips 2+ only — copy from previous clip's LAST FRAME exactly]
2. FACE LOCK STATEMENT: "⚠️ चेहरा पूरी तरह स्थिर और क्लिप 1 के समान रहेगा — चेहरे की बनावट, त्वचा का रंग, आँखें, होंठ, बाल — कोई परिवर्तन नहीं।"
3. OUTFIT & APPEARANCE: [Full locked outfit + full locked appearance — copy verbatim, ZERO shortcuts]
4. LOCATION: [Copy LOCKED BACKGROUND verbatim from clip 1 + freeze line — identical in every clip]
5. ACTION: [ONE emotion OR one physical action — never both simultaneously. Body stays still during emotion changes.]
6. DIALOGUE: [Strictly 15-19 Hindi words. COUNT THEM. Format: चरित्र: "(बातचीत के लहजे में...) संवाद"]
7. AUDIO: [Same BGM mood/tempo as previous clip — never change music style mid-video]
8. CAMERA: [Static shot angle + distance]. ALWAYS include: "Ultra-sharp focus, 8k resolution, highly detailed. कैमरा बिल्कुल स्थिर।"
9. LIGHTING: [Same lighting as clip 1 — direction, color temperature, quality must match]. ALWAYS include: "Cinematic contrast, photorealistic skin texture, extremely crisp."
10. LAST FRAME: [Character: exact position + expression + hand placement. Background: full object inventory by shelf/position. Camera: angle + distance. Lighting: direction + temperature.]
11. Background should look more realistic and less "AI-generated" with every clip as the character interacts with it — this builds viewer trust and immersion. Never introduce new objects or remove existing ones, but feel free to show them from slightly different angles or with different lighting reflections as the character moves.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHARACTER DRIFT PREVENTION — IRON RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- The character's face, hair, skin tone, and build MUST be pixel-identical to clip 1.
  Never write age, weight, or appearance variations.
- NEVER describe the character's emotion in the appearance block — only in the ACTION block.
- NEVER write "she now looks", "he appears", "looking more" — these trigger temporal drift.
- NEVER use pronouns like "her hair" or "his eyes" in the appearance block without the full description.
- Lip color, eye makeup, earrings — if present in clip 1, state them verbatim in every clip's
  OUTFIT & APPEARANCE block.
- Every clip must re-establish the appearance as if it's the first clip. Never rely on "as before" or
  "same as previous".

AUDIO-VISUAL SYNC:
Add to every prompt: "Audio-visual sync: match lip movements precisely to spoken dialogue."

VISUAL FORMAT PROHIBITIONS:
Add to every prompt: "No cinematic letterbox bars. No black bars. Full {ar} frame edge to edge.
No burned-in subtitles. No text overlays. No lower thirds. No captions. No watermarks.
No on-screen app UI. If showing phone, show dark screen only."

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
            temperature=0.4,  # Very low temperature = maximum rule compliance
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
        f"This description is the ONLY thing preventing visual drift — be exhaustive.\n\n"
        f"MANDATORY sections to cover:\n\n"
        f"1. CHARACTER FACE (most critical — describe every detail you can see):\n"
        f"   - Exact skin tone (warm/cool/neutral, dark/medium/light — be specific)\n"
        f"   - Face shape visible in this frame\n"
        f"   - Eye color, shape, any makeup visible\n"
        f"   - Lip color and fullness\n"
        f"   - Hair: color, texture, how it falls in this exact frame\n"
        f"   - Any distinctive marks visible\n\n"
        f"2. CHARACTER STATE:\n"
        f"   - Exact body position (standing/sitting, which direction facing)\n"
        f"   - Expression: precise micro-expression\n"
        f"   - Both hands: exact position and what they are holding/touching\n"
        f"   - What the character just finished saying or doing\n\n"
        f"3. BACKGROUND INVENTORY (critical — Veo hallucinates new objects if this is vague):\n"
        f"   List each object individually:\n"
        f"   'Left shelf: [count] [color] [shape] items. Center shelf: ... Right shelf: ...'\n"
        f"   Wall color and texture. Counter/floor material and color.\n\n"
        f"4. CAMERA:\n"
        f"   - Exact angle (eye-level / slightly low / slightly high)\n"
        f"   - Exact distance (extreme close-up / close-up / medium / medium-wide)\n"
        f"   - Movement state (always write 'camera absolutely still' unless clearly moving)\n\n"
        f"5. LIGHTING LOCK:\n"
        f"   - Direction, color temperature, quality, shadow direction\n\n"
        f"6. FACE LOCK STATEMENT to inject into next prompt:\n"
        f"   Write: '⚠️ FACE LOCK: The character's face is [describe exact features seen in these frames]. "
        f"This face must remain 100% identical — same bone structure, same skin tone, same eyes, same lips.'\n\n"
        f"Format: start directly with 'CONTINUING FROM:' — no preamble.\n"
        f"Maximum 250 words. Be exhaustively specific. Factual, not poetic.\n"
        f"Every vague word is a drift risk — replace with exact specifics."
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
      2. Send to Gemini → get accurate CONTINUING FROM: description (includes face analysis)
      3. Replace the prompt's CONTINUING FROM: with the Gemini-generated one
      4. Extract the single absolute last frame as I2V starting image for Veo
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


def sanitize_prompt_for_veo(client, prompt: str, clip_num: int) -> str:
    """
    Run every prompt through Gemini BEFORE sending to Veo.
    Strips anything that triggers Veo's content policy silently.
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

IMPROVEMENT CLAIMS:
- "बेहतर होगा / होगी" → replace with "अच्छा लगेगा"
- "सब ठीक हो जाएगा" → "सब साफ दिखेगा"
- "सुधार होगा" → remove or replace with confidence framing

ABSOLUTE RULES:
1. NEVER change: outfit descriptions, CONTINUING FROM blocks, LAST FRAME blocks, FACE LOCK blocks,
   camera/lighting/location descriptions, no-letterbox/no-subtitle lines, the ⚠️ face lock statement
2. FOR APPEARANCE BLOCKS: keep physical description (face shape, eyes, hair, build, skin tone)
   but REMOVE any language about skin condition improvement or transformation
3. PRESERVE full prompt length — every removed phrase gets a safe replacement
4. Keep all Hindi — just swap blocked words/phrases
5. Keep character names and speaker-colon dialogue format
6. Output the sanitized prompt ONLY — no preamble, no explanation, no markdown"""

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
                f"- Outfit + full physical appearance description\n"
                f"- ⚠️ FACE LOCK statement and ⚠️ चेहरा पूरी तरह स्थिर lines\n"
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
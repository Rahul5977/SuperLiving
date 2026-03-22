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
"""
REPLACEMENT for the `build_clip_prompts` function in backend/ai_engine.py
Replace the entire function — from `def build_clip_prompts(` to the final `return data["clips"]`
"""

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
) -> list:
    ratio_map = {
        "9:16 (Reels / Shorts)": "9:16 vertical portrait",
        "16:9 (YouTube / Landscape)": "16:9 horizontal landscape",
        "9:16": "9:16 vertical portrait",
        "16:9": "16:9 horizontal landscape",
    }
    ar = ratio_map.get(aspect_ratio, "9:16 vertical portrait")
    extra_section = f"\nPRODUCTION NOTES: {extra_prompt}" if extra_prompt.strip() else ""

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

    system = f"""You are a senior AI video director specialising in ultra-realistic, hallucination-free Veo 3.1 ad generation for Indian audiences.

════════════════════════════════════════════════════════════
TASK
════════════════════════════════════════════════════════════
Split the SuperLiving ad script into exactly {num_clips} sequential 8-second clip prompts.
EVERY PROMPT MUST BE WRITTEN IN DEVANAGARI HINDI (no exceptions).
Output valid JSON only — structure shown at the bottom.

════════════════════════════════════════════════════════════
CHARACTER CONSISTENCY
════════════════════════════════════════════════════════════
{char_block}

RULES:
1. OUTFIT & APPEARANCE block appears in EVERY clip — verbatim, never shortened.
2. Never add emotions, age changes, weight changes, or mood adjectives to the appearance block.
3. Never use phrases like "अब वह दिखती है", "अब वह लगता है", "more confident now" — these cause face drift.
4. Earrings, moles, scars, watch — if present in clip 1, state them identically in every clip.

════════════════════════════════════════════════════════════
LIGHTING — GHOST FACE PREVENTION (CRITICAL)
════════════════════════════════════════════════════════════
RULE: Never use a single overhead or bottom-up light source alone.
      A single top-down light creates black eye sockets = horror/ghost face.
      A single bottom-up light (e.g., phone screen only) = skull effect.

MANDATORY DUAL-SOURCE LIGHTING in every clip:
  PRIMARY: Soft warm side-fill from LEFT or RIGHT (table lamp, window, ambient glow).
           This fills eye sockets and makes the face human and readable.
  SECONDARY: Ambient overhead or background glow — very low intensity.

Always end the LIGHTING block with:
"⚠️ आँखें और माथा CLEARLY VISIBLE हैं। कोई काले eye socket shadows नहीं।
Cinematic contrast, photorealistic skin texture, extremely crisp."

════════════════════════════════════════════════════════════
RULE OF ONE ACTION (NO EXCEPTIONS)
════════════════════════════════════════════════════════════
Each clip shows ONE state — never a transition or sequence.

FORBIDDEN patterns in ACTION block:
✗ "expression changes from sad to happy" → split into 2 clips
✗ "looks down at phone, then back at camera" → 2 actions, forbidden
✗ "expression softens AND eyes light up AND he nods" → 3 actions, forbidden
✗ "raises hand into frame" mid-clip → hands must be in established position from clip start
✗ "slowly smiles" / "gradually becomes confident" → transitions cause drift

CORRECT pattern:
✓ ONE static emotional state: "चेहरे पर शांत आत्मविश्वास है" — body still, hands in lap
✓ ONE pre-established physical state: character is already holding something from clip start

HANDS RULE — CRITICAL FOR SEAMLESS STITCHING:
Hands must be OUT OF FRAME or COMPLETELY STILL throughout.

WHY: Veo generates 8 seconds of movement. If hands are mid-gesture at
the LAST FRAME of clip N, they will be in a DIFFERENT position at the
FIRST FRAME of clip N+1. This creates an instant visual jump at every
clip boundary — the most visible form of stutter.

FORBIDDEN in ACTION block:
  ✗ Any hand gesture: "वह हाथ से इशारा करता है" — will cause jump cut
  ✗ Hand moves during emotion change: "वह हाथ उठाकर..." — forbidden
  ✗ Picking up / putting down objects mid-clip

REQUIRED in every ACTION block:
  "शरीर बिल्कुल स्थिर रहता है।
  हाथ गोद में रखे हुए हैं, फ्रेम से बाहर।
  कोई हाथ का इशारा नहीं।"

If a physical action IS needed (e.g., holding phone):
  - The object must be established IN FRAME from the very first frame of the clip
  - It cannot enter or leave frame mid-clip
  - State it in CONTINUING FROM: "दाहिने हाथ में काली स्क्रीन वाला फोन,
    फ्रेम के नीचे की ओर स्थिर"

════════════════════════════════════════════════════════════
SHOT TYPE LOCK — PREVENTS JARRING CUTS
════════════════════════════════════════════════════════════
Pick ONE shot type for the entire video and use it in EVERY clip.
NEVER mix shot types between clips.

RECOMMENDED for talking-head UGC ads:
  मीडियम क्लोज-अप शॉट (MCU) — character visible from mid-chest up.
  This shows face clearly, hides body/hand movement, looks like real UGC.

FORBIDDEN shot combinations:
  ✗ MCU in clip 1 → Medium shot in clip 2 (body suddenly more visible = jarring)
  ✗ Close-up in clip 3 → MCU in clip 4 (face suddenly shrinks = jarring)
  ✗ Any camera movement (zoom, pan, tilt) — always STATIC

POSTURE LOCK:
  Decide once: is the character SITTING or STANDING?
  State this in every CONTINUING FROM and LAST FRAME block.
  Veo will not maintain posture across clips unless explicitly told.
  SEATED is better for UGC — sitting = intimate, confessional, real.

CAMERA LINE TO USE IN EVERY CLIP:
  "मीडियम क्लोज-अप शॉट, आई-लेवल पर (STATIC SHOT)।
  Ultra-sharp focus, 8k resolution, highly detailed. कैमरा बिल्कुल स्थिर।"

════════════════════════════════════════════════════════════
BACKGROUND FREEZE — MOST CRITICAL ANTI-HALLUCINATION RULE
════════════════════════════════════════════════════════════
STEP 1: Before writing clip 1, compose one LOCKED BACKGROUND description of ≥60 words.
  Include: exact wall color + texture, every object (position: left/center/right, color,
  shape, count), floor material, light source position and color temperature,
  any furniture edges visible.

  GOOD EXAMPLE:
  "LOCKED BACKGROUND: हल्के बेज रंग (#F5F0E8) की दीवार, हल्की बनावट के साथ।
  पीछे गहरे भूरे रंग की लकड़ी की बुकशेल्फ — ऊपरी शेल्फ पर बाईं तरफ 5 मोटी
  UPSC किताबें, बीच में एक छोटा ग्लोब, दाईं तरफ नोट्स के 3 बंडल; बीच की शेल्फ
  पर बिखरे कागज़, एक खाली सफ़ेद मग, 2 नीले पेन; निचली शेल्फ पर 6 इतिहास की
  किताबें। दाहिनी ओर से स्टडी लैंप की गर्म पीली रोशनी। फर्श पर भूरा कालीन।"

STEP 2: Copy this EXACT sentence VERBATIM into the LOCATION block of EVERY SINGLE clip.
  Word for word. No paraphrasing. No shortening.

STEP 3: End EVERY clip's LOCATION block with this freeze line (verbatim):
  "पृष्ठभूमि पूरी तरह स्थिर और अपरिवर्तित रहती है — कोई नई वस्तु नहीं आएगी,
  कोई वस्तु गायब नहीं होगी, रंग नहीं बदलेगा।"

VIOLATION: If any clip's LOCATION differs from clip 1 — that is a fatal error.

════════════════════════════════════════════════════════════
DIALOGUE — THE LIP-SYNC GOLDILOCKS ZONE
════════════════════════════════════════════════════════════
STRICT LIMIT: 15–19 Hindi words per clip. Count every word before writing.
- Under 15 words → slow-motion speech, awkward silence
- Over 19 words → chipmunk rush, broken lip-sync
- Exactly 15–19 → perfect 8-second sync

FORBIDDEN dialogue patterns:
✗ Voiceover: NEVER assign dialogue to a character not visible in frame.
  "ऋषिका (वॉयसओवर):" → Veo has no face to sync to → silence or random mouth movement.
  FIX: On-screen character quotes the off-screen person:
  राहुल: "(बातचीत के लहजे में) ऋषिका ने कहा — 'बस एक sentence बोलना।'"

✗ Multiple speakers in one clip: Only ONE character speaks per clip.

FORMAT: चरित्र: "(बातचीत के लहजे में, [emotion]) संवाद"
Always start the bracket with "(बातचीत के लहजे में..." — this stabilises Veo's voice engine.

════════════════════════════════════════════════════════════
CONTINUITY RULES
════════════════════════════════════════════════════════════
Every clip except clip 1 MUST begin with CONTINUING FROM: block.
The CONTINUING FROM block MUST contain:
  - Character: exact expression, exact body position, exact hand position
  - Background: full object inventory (every shelf item by position)
  - Camera: shot type and framing
  - Lighting: direction and color temperature

Every clip MUST end with a LAST FRAME: block using the same format.
LAST FRAME becomes the CONTINUING FROM of the next clip — they must match exactly.

════════════════════════════════════════════════════════════
PHONE SCREEN TRAP
════════════════════════════════════════════════════════════
If any character holds or looks at a phone:
- Screen MUST be black: "फोन की स्क्रीन काली है — कोई UI, text, app या face नहीं।"
- NEVER describe a chat interface, message bubbles, or profile picture on screen.
- NEVER show a second character's face inside a phone screen.
- Veo WILL hallucinate a face/UI if not explicitly blocked.

════════════════════════════════════════════════════════════
SCENE CHANGE RULE (multiple locations or characters)
════════════════════════════════════════════════════════════
If a clip introduces a completely new character or new location:
- CONTINUING FROM must explicitly state: "यह एक नया, स्वतंत्र दृश्य है। पिछले
  क्लिप के चरित्र और पृष्ठभूमि यहाँ नहीं हैं।"
- The new character MUST have their own FACE LOCK STATEMENT — never reference
  a different character's face lock.
- New character's full OUTFIT & APPEARANCE must be written from scratch.

════════════════════════════════════════════════════════════
REALISM RULES — WHAT MAKES IT LOOK REAL, NOT AI
════════════════════════════════════════════════════════════
1. SETTING: Lived-in, slightly imperfect spaces. Slight wear on furniture.
   Books at random angles. A used mug. Real spaces, not staged.

2. EXPRESSIONS: Subtle, not theatrical. "हल्की सी मुस्कान" not "चौड़ी खुश मुस्कान".
   Real people show micro-expressions — slight eyebrow raise, lip corner lift.

3. LIGHTING: Natural sources only — window light, table lamp, overhead tube.
   Never describe "cinematic key light" or "studio setup" in casual scenes.
   For office/indoor: warm overhead ambient + side fill.
   For bedroom/night: lamp glow from side, no overhead.
   For outdoor: diffuse daylight from above, slight shadow under chin.

4. SKIN: Always include: "photorealistic skin texture, visible pores, natural skin tone,
   no airbrushing, no smoothing." This forces Veo to render real skin.

5. CAMERA: Always STATIC. Never pan, zoom, or track. Static shots = real UGC feel.
   Shot type: medium close-up (MCU) for talking head. Medium shot (MS) for body language.

6. HAIR: Specify exact style once in clip 1 — Veo drifts on hair. Repeat verbatim.
   Include: length, texture (straight/wavy/curly), styling (parted/tied/loose).

7. MICRO-DETAILS THAT PREVENT DRIFT: Scars, moles, watch type, jewelry —
   state these in every clip. They act as identity anchors.

8. CONTINUOUS DIALOGUE: If a conversation spans multiple clips, maintain the same emotional tone and energy level in the dialogue across clips. This prevents Veo from randomly changing the character's mood.

════════════════════════════════════════════════════════════
MANDATORY SECTIONS IN EVERY CLIP PROMPT
════════════════════════════════════════════════════════════
Use this exact section order:

1. CONTINUING FROM: [clips 2+ only — full last-frame state + background inventory]
2. FACE LOCK STATEMENT: ⚠️ चेहरा पूरी तरह स्थिर और क्लिप 1 के समान रहेगा —
   चेहरे की बनावट, त्वचा का रंग, आँखें, होंठ, बाल — कोई परिवर्तन नहीं।
3. OUTFIT & APPEARANCE: [verbatim locked outfit + full appearance — no paraphrase]
4. LOCATION: [verbatim LOCKED BACKGROUND from clip 1 + freeze line]
5. ACTION: (STATIC SHOT) [ONE state only. "शरीर बिल्कुल स्थिर रहता है, हाथ नीचे।"]
6. DIALOGUE: [15–19 words. चरित्र: "(बातचीत के लहजे में...) संवाद"]
7. AUDIO: [BGM description — same mood/tempo across all clips unless story requires shift]
8. CAMERA: [Shot type + eye-level + "Ultra-sharp focus, 8k resolution, highly detailed.
   कैमरा बिल्कुल स्थिर।"]
9. LIGHTING: [Dual source description. "⚠️ आँखें clearly visible। कोई काले eye socket
   shadows नहीं। Cinematic contrast, photorealistic skin texture, extremely crisp."]
10. VISUAL FORMAT PROHIBITIONS: No cinematic letterbox bars. No black bars. Full {ar}
    frame edge to edge. No burned-in subtitles. No text overlays. No lower thirds.
    No captions. No watermarks. No on-screen app UI. If showing phone, show dark screen only.
    Audio-visual sync: match lip movements precisely to spoken dialogue.
11. LAST FRAME: [exact expression + body + hand position + full background inventory
    + camera type + lighting — this becomes the next clip's CONTINUING FROM]

════════════════════════════════════════════════════════════
SELF-CHECK BEFORE OUTPUTTING EACH CLIP
════════════════════════════════════════════════════════════
Before writing each clip's JSON, verify:
□ Word count of DIALOGUE: counted, 15–19 Hindi words?
□ ACTION block: exactly ONE state? No transitions? No hand movement?
□ LIGHTING: two sources? Eyes visible? Ghost face prevented?
□ LOCATION: verbatim copy from clip 1? Freeze line present?
□ LAST FRAME: complete? Matches what CONTINUING FROM of next clip will need?
□ Voiceover: zero? All dialogue assigned to on-screen speaker only?
□ Phone (if shown): black screen instruction present?
□ FACE LOCK: present? References correct character?

If any check fails — fix before outputting.

════════════════════════════════════════════════════════════
OUTPUT FORMAT
════════════════════════════════════════════════════════════
Valid JSON only. No markdown. No preamble. No explanation after the JSON.

{{
  "clips": [
    {{
      "clip": 1,
      "scene_summary": "one English sentence describing what happens",
      "last_frame": "exact last-frame state in Hindi — expression, position, background",
      "prompt": "full Hindi prompt following the 11-section structure above"
    }}
  ]
}}

Generate exactly {num_clips} clips."""

    user_text = (
        f"SUPERLIVING AD SCRIPT:\n{script}"
        f"{char_sheet_injection}"
        f"{extra_section}"
        f"\n\nNow generate exactly {num_clips} clip prompts as JSON."
    )

    contents = [types.Part.from_text(text=user_text)]
    if extra_image_parts:
        contents.append(types.Part.from_text(
            text=f"\n\nREFERENCE IMAGES ({len(extra_image_parts)}): "
                 f"Match the visual tone, setting, and mood shown."
        ))
        contents.extend(extra_image_parts)

    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.15,  # Lower = stricter rule following, less creative drift
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
        f"The NEXT clip is: '{next_scene_summary}'\n\n"
        f"Write a CONTINUING FROM: block for the next Veo prompt. "
        f"Be exhaustively specific — every vague word is a drift risk.\n\n"
        f"MANDATORY — cover ALL of these exactly:\n\n"
        f"1. SHOT TYPE & FRAMING (critical — prevents shot-size drift between clips):\n"
        f"   State the exact shot: 'medium close-up (character from mid-chest up)'\n"
        f"   OR 'medium shot (character from waist up)' OR 'close-up (face and shoulders)'\n"
        f"   The next clip MUST use this EXACT same framing.\n\n"
        f"2. CHARACTER POSTURE:\n"
        f"   Seated or standing? If seated: what surface, which direction facing.\n"
        f"   If standing: feet position, lean direction.\n"
        f"   This is the #1 cause of jarring cuts — posture MUST match clip-to-clip.\n\n"
        f"3. HAND & ARM POSITION:\n"
        f"   Exact resting position of BOTH hands. Are they in frame or out of frame?\n"
        f"   If in frame: where exactly (lap, desk, clasped, etc.)\n\n"
        f"4. EXPRESSION:\n"
        f"   Precise micro-expression. Not 'looks sad' but:\n"
        f"   'lips slightly parted, slight furrow in brow, eyes looking directly at camera'\n\n"
        f"5. BACKGROUND INVENTORY:\n"
        f"   List EVERY visible object by position (left/center/right).\n"
        f"   Wall color. Any windows or light sources visible.\n"
        f"   Floor material if visible.\n\n"
        f"6. LIGHTING STATE:\n"
        f"   Which side. Color temperature (warm/cool/neutral). Soft or hard.\n"
        f"   Copy this verbatim into next clip's LIGHTING block.\n\n"
        f"7. CAMERA:\n"
        f"   Eye-level / slightly above / slightly below.\n"
        f"   State: 'camera absolutely still, no movement'\n\n"
        f"Format: start with 'CONTINUING FROM:' — no preamble. Max 220 words."
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
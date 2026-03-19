import base64
import json
import logging
import urllib.request
import urllib.error

from google.genai import types

logger = logging.getLogger(__name__)

# PHASE 1 — Parser Agent
def parse_script_for_characters(client, script: str) -> dict:
    """
    Use Gemini to read the ad script and output JSON containing an array of
    characters. Each character has: id, name, physical_baseline, outfit.
    """
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=(
            f"Read the following ad script carefully and extract every named or "
            f"described character.\n\n"
            f"For EACH character return a JSON object with these keys:\n"
            f"  - \"id\": a unique slug like \"char_1\", \"char_2\", etc.\n"
            f"  - \"name\": the character's name or role as given in the script.\n"
            f"  - \"physical_baseline\": describe ONLY the neutral, static physical "
            f"traits (exact skin tone, face shape, eye shape/color/spacing, brow shape, nose shape, "
            f"lip fullness+color, jawline, cheekbones, hair color/texture/length/style, "
            f"age range, build, any distinctive marks). "
            f"DO NOT include any time-based changes, temporary emotions, or expressions.\n"
            f"  - \"outfit\": the exact garment the character wears (color, fabric, "
            f"pattern, fit, neckline). Be exhaustively specific. One sentence.\n\n"
            f"Return ONLY valid JSON in this format:\n"
            f"{{\n"
            f"  \"characters\": [\n"
            f"    {{\"id\": \"char_1\", \"name\": \"...\", "
            f"\"physical_baseline\": \"...\", \"outfit\": \"...\"}},\n"
            f"    ...\n"
            f"  ]\n"
            f"}}\n"
            f"No markdown, no preamble, no explanation.\n\n"
            f"AD SCRIPT:\n{script}"
        ),
    )

    if response is None or response.text is None:
        raise RuntimeError("Gemini returned empty response when parsing characters")

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    data = json.loads(raw)
    return data

# PHASE 2 — Imagen Agent
def auto_generate_character_image(api_key: str, physical_baseline: str, outfit: str) -> str:
    """
    Call the Google Imagen 3 API to generate a photorealistic 9:16 portrait.
    Returns the base64-encoded image string.
    """
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"imagen-3.0-generate-001:predict?key={api_key}"
    )

    prompt = (
        f"Hyper-realistic smartphone photo of an everyday Indian person. "
        f"{physical_baseline}. Wearing {outfit}. "
        f"About 70% of the body is visible (head to knees), centered in frame. "
        f"Casual indoor setting, lived-in and not staged. "
        f"Shot on an ordinary smartphone: uneven exposure, slight grain, natural daylight. "
        f"Ultra-realistic natural skin texture, visible pores, no airbrushing. "
        f"No cinematic lighting, no dramatic shadows, completely unretouched. "
        f"Looks like a real person recording a high-trust UGC video at home."
    )

    payload = json.dumps({
        "instances": [{"prompt": prompt}],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": "9:16",
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        logger.error(f"Imagen API error {e.code}: {error_body}")
        raise RuntimeError(f"Imagen API returned HTTP {e.code}: {error_body}") from e

    predictions = body.get("predictions", [])
    if not predictions:
        raise RuntimeError("Imagen API returned no predictions")

    b64_image = predictions[0].get("bytesBase64Encoded", "")
    if not b64_image:
        raise RuntimeError("Imagen API returned empty image data")

    return b64_image


# PHASE 3 — Director Agent
def build_director_prompts(client, script: str, characters_json: dict, num_clips: int) -> list:
    """
    Returns a list of clip dicts: [{clip, scene_summary, last_frame, prompt}].
    """

    # Build character context block with exhaustive detail
    char_lines = []
    for char in characters_json.get("characters", []):
        char_lines.append(
            f"[{char['name']}] LOCKED APPEARANCE (copy verbatim into every prompt, zero shortcuts):\n"
            f"  {char.get('physical_baseline', '')}"
        )
        char_lines.append(
            f"[{char['name']}] LOCKED OUTFIT (copy verbatim into every prompt, zero shortcuts):\n"
            f"  {char.get('outfit', '')}"
        )
    character_block = "\n".join(char_lines)

    # Build system prompt
    system = f"""You are an expert AI video director creating prompts for Google Veo 3.1.

TASK: Split the given SuperLiving ad script into exactly {num_clips} sequential 8-second clip prompts.
ALL CLIP PROMPTS MUST BE WRITTEN IN DEVANAGARI HINDI.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHARACTER FACE LOCK — THE SINGLE MOST CRITICAL RULE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The viewer watches a continuous ad. If the character's face changes by 1% between clips,
the viewer's brain detects it immediately — the ad loses credibility and the viewer scrolls away.

FACE LOCK RULES:
1. Line 1 of EVERY prompt: "[Name] is wearing [locked outfit verbatim]. [locked appearance verbatim]."
2. Write the FULL appearance text. Not a summary. Not a reference. The entire text, word for word.
3. Every clip must re-establish the character as if it's clip 1 — never rely on "as before" or "same as previous".
4. Include section: "⚠️ चेहरा पूरी तरह स्थिर और क्लिप 1 के समान रहेगा — चेहरे की बनावट, त्वचा का रंग, आँखें, होंठ, बाल — कोई परिवर्तन नहीं।"
5. The character in clip 6 must be the SAME human being as clip 1 — identical bone structure, skin, eyes, lips.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VIEWER ENGAGEMENT — COMPELLING, WATCHABLE AD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The person watching this ad on their phone must be engaged within 2 seconds or they scroll away:
- Character always faces camera naturally with warm, relatable energy
- Eye contact with camera feels personal (like talking to a friend)
- Natural hand gestures that feel real, not stiff
- Warm, well-lit face — viewer must feel connection and trust
- Emotional arc: problem → recognition → curiosity → solution → confidence

THE "RULE OF ONE ACTION" & CAMERA GEOMETRY (CRITICAL FOR VEO):
- ACTION ISOLATION: Never overload an 8-second clip.
  If a character changes emotion, body MUST remain still: "शरीर बिल्कुल स्थिर रहता है, हाथ नीचे ही रहेंगे"
- CAMERA LOCK: "(STATIC SHOT) / कैमरा बिल्कुल स्थिर रहता है" — no zoom/pan during character movement
- LOCATION LOCK — BACKGROUND FREEZE (MOST CRITICAL ANTI-HALLUCINATION RULE):
  STEP 1: Write one LOCKED BACKGROUND sentence (50+ words) describing wall, shelves, items by position, floor, lighting.
  STEP 2: Copy this EXACT sentence VERBATIM into the LOCATION block of EVERY clip.
  STEP 3: End every LOCATION block with:
  "पृष्ठभूमि पूरी तरह स्थिर और अपरिवर्तित रहती है — कोई नई वस्तु नहीं आएगी, कोई वस्तु गायब नहीं होगी, रंग नहीं बदलेगा।"
  VIOLATION: Different LOCATION in any clip = fatal error.

UI & HALLUCINATION GUARDRAILS:
- Phone screen must always be: "फोन की स्क्रीन काली है". NEVER describe an app UI or video call.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DIALOGUE — LIP-SYNC GOLDILOCKS ZONE (EXTREMELY IMPORTANT)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- STRICT LIMIT: Exactly 15 to 19 Hindi words per clip. COUNT EVERY WORD after writing.
- Fewer than 15 → slow-motion speech. More than 20 → chipmunk speed, broken lip-sync.
- REWRITE if count is wrong. This is non-negotiable.
- Always start with: '(बातचीत के लहजे में...)' to lock the AI voice tone.
- Format: चरित्र: "(बातचीत के लहजे में, [emotion]) [dialogue]"

CONTINUITY RULES:
- Every prompt except clip 1 MUST begin with CONTINUING FROM: (copy from previous clip's LAST FRAME exactly)
- Include full background inventory in CONTINUING FROM block
- End every prompt with LAST FRAME: [exact position, expression, hands, camera, full background]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CLIP PROMPT STRUCTURE — ALL SECTIONS MANDATORY IN EVERY CLIP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. CONTINUING FROM: [Clips 2+ only]
2. FACE LOCK STATEMENT: "⚠️ चेहरा पूरी तरह स्थिर और क्लिप 1 के समान रहेगा — चेहरे की बनावट, त्वचा का रंग, आँखें, होंठ, बाल — कोई परिवर्तन नहीं।"
3. OUTFIT & APPEARANCE: [Full locked outfit + full locked appearance — verbatim, zero shortcuts]
4. LOCATION: [Verbatim LOCKED BACKGROUND + freeze line — identical every clip]
5. ACTION: [ONE emotion OR one physical action — never both simultaneously]
6. DIALOGUE: [15-19 Hindi words. COUNT THEM. Format: चरित्र: "(बातचीत के लहजे में...) संवाद"]
7. AUDIO: [Same BGM mood/tempo throughout — never change music style]
8. CAMERA: [Static angle + distance]. Include: "Ultra-sharp focus, 8k resolution, highly detailed. कैमरा बिल्कुल स्थिर।"
9. LIGHTING: [Identical to clip 1 — direction, temperature, quality]. Include: "Cinematic contrast, photorealistic skin texture, extremely crisp."
10. LAST FRAME: [Character: exact position + expression + hand placement. Background: full object inventory. Camera: angle + distance. Lighting: direction + temperature.]

CHARACTER DRIFT PREVENTION — IRON RULES:
- Face, hair, skin tone, build MUST be pixel-identical across all clips.
- NEVER write "she now looks", "he appears more", "looking confident now" — these trigger temporal drift.
- Lighting LOCKED to clip 1. State "soft light from left side" (or whatever clip 1 has) in EVERY clip.

AUDIO-VISUAL SYNC:
Add to every prompt: "Audio-visual sync: match lip movements precisely to spoken dialogue."

VISUAL FORMAT PROHIBITIONS:
Add to every prompt: "No cinematic letterbox bars. No black bars. Full 9:16 vertical frame edge to edge.
No burned-in subtitles. No text overlays. No watermarks. No on-screen app UI."

OUTPUT: valid JSON only:
{{
  "clips": [
    {{"clip": 1, "scene_summary": "...", "last_frame": "...", "prompt": "..."}},
    ...
  ]
}}"""

    user_text = (
        f"SUPERLIVING AD SCRIPT:\n{script}\n\n"
        f"LOCKED CHARACTER PROFILES (copy these verbatim into every clip's OUTFIT & APPEARANCE section):\n"
        f"{character_block}\n\n"
        f"Generate exactly {num_clips} clip prompts as JSON now."
    )

    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=[types.Part.from_text(text=user_text)],
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.15,  # Very low temperature = maximum rule adherence
        ),
    )

    if response is None or response.text is None:
        raise RuntimeError("Gemini returned empty response when building director prompts")

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    data = json.loads(raw)
    return data["clips"]
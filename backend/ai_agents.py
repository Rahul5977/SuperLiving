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
    characters.  Each character has: id, name, physical_baseline, outfit.
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
            f"traits (skin tone, face shape, eye shape/color, brow shape, nose shape, "
            f"lip fullness, jawline, hair color/texture/length/style, age range, build). "
            f"DO NOT include any time-based changes, temporary emotions, or expressions.\n"
            f"  - \"outfit\": the exact garment the character wears (color, fabric, "
            f"pattern, fit). One sentence.\n\n"
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

    # Build character context block
    char_lines = []
    for char in characters_json.get("characters", []):
        char_lines.append(
            f"[{char['name']}] LOCKED APPEARANCE: {char['physical_baseline']}"
        )
        char_lines.append(
            f"[{char['name']}] LOCKED OUTFIT: {char['outfit']}"
        )
    character_block = "\n".join(char_lines)

    # Build system prompt
    system = f"""You are an expert AI video director creating prompts for Google Veo 3.1.

TASK: Split the given SuperLiving ad script into exactly {num_clips} sequential 8-second clip prompts.
ALL CLIP PROMPTS MUST BE WRITTEN IN DEVANAGARI HINDI.

CHARACTER CONSISTENCY — DUAL LOCK (image + text):
1. First line of every prompt MUST be: "[Name] is wearing [locked outfit] and [locked appearance]."
2. Do NOT paraphrase or add emotions to the physical appearance block.

THE "RULE OF ONE ACTION" & CAMERA GEOMETRY (CRITICAL FOR VEO):
Diffusion models 'melt' if overloaded. You MUST follow these isolation rules:
- ACTION ISOLATION: Never overload an 8-second clip. If a character changes emotion (e.g., sad to happy), their body MUST remain absolutely still (write: "शरीर बिल्कुल स्थिर रहता है, हाथ नीचे ही रहेंगे").
- If a character does a physical action (dropping products, lifting phone), their emotion must already be established.
- CAMERA LOCK: Whenever a character moves their hands or body, you MUST use "(STATIC SHOT) / कैमरा बिल्कुल स्थिर रहता है". Do NOT zoom or pan while a character is moving.
- LOCATION LOCK: Create a highly specific 'LOCKED BACKGROUND' description (e.g., specific shelf layout, blurred background) and copy it VERBATIM into the LOCATION block of every single clip so the room never changes shape. Use hard cuts for scene changes.

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
- End every prompt with: "LAST FRAME: [exact position, expression, camera, framing]"

AUDIO-VISUAL SYNC:
Add to every prompt: "Audio-visual sync: match lip movements precisely to spoken dialogue."

VISUAL FORMAT PROHIBITIONS:
Add to every prompt: "No cinematic letterbox bars. No black bars. Full 9:16 vertical frame edge to edge. No burned-in subtitles. No text overlays. No watermarks. No on-screen app UI."

OUTPUT: valid JSON only:
{{
  "clips": [
    {{"clip": 1, "scene_summary": "...", "last_frame": "...", "prompt": "..."}},
    ...
  ]
}}"""

    user_text = (
        f"SUPERLIVING AD SCRIPT:\n{script}\n\n"
        f"LOCKED CHARACTER PROFILES:\n{character_block}\n\n"
        f"Generate exactly {num_clips} clip prompts as JSON now."
    )

    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=[types.Part.from_text(text=user_text)],
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.4,
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

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

CLIP PROMPT STRUCTURE — MANDATORY SECTIONS (every section must appear in every clip):
1. CONTINUING FROM: [Clips 2+ only — includes full background inventory]
2. OUTFIT & APPEARANCE: [Copy locked outfit + appearance verbatim — do NOT paraphrase]
3. LOCATION: [Copy LOCKED BACKGROUND verbatim from clip 1 + freeze line — identical in every clip]
4. ACTION: [ONE emotion OR one physical action — never both simultaneously]
5. DIALOGUE: [Strictly 15-19 Hindi words. Count them. Format: चरित्र: "(बातचीत के लहजे में...) संवाद"]
6. AUDIO: [Same BGM mood/tempo throughout — never change music style mid-video]
7. CAMERA: [Static angle + distance]. ALWAYS include: "Ultra-sharp focus, 8k resolution, highly detailed. कैमरा बिल्कुल स्थिर।"
8. LIGHTING: [IDENTICAL to clip 1 — same direction, color temperature, quality]. ALWAYS include: "Cinematic contrast, photorealistic skin texture, extremely crisp."
9. LAST FRAME: [Character: exact position + expression + hand placement. Background: full object inventory. Camera: angle + distance. Lighting: direction + temperature.]

CHARACTER DRIFT PREVENTION — IRON RULES:
- The character's face, hair, skin tone, and build MUST be pixel-identical across all clips. Never vary age, weight, or features.
- NEVER describe the character's emotion in the appearance block — only in the ACTION block.
- NEVER write "she now looks", "he appears more", "looking confident now" — these trigger temporal drift.
- Lip color, eye makeup, earrings — if present in clip 1, state them verbatim in every clip's OUTFIT & APPEARANCE block.
- Lighting must be LOCKED to clip 1. If clip 1 has soft left-side light, every clip must state "soft light from left side" — Veo will hallucinate new lighting if you omit this.

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
            temperature=0.2,  # Low temperature = maximum rule adherence, minimum hallucination
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
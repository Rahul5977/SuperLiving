import streamlit as st
import time
import subprocess
import os
import json
import tempfile
import shutil
import shutil as _shutil
import traceback
import re
import urllib.request
try:
    import imageio_ffmpeg
except ImportError:
    imageio_ffmpeg = None
from google import genai
from google.genai import types

# ── Portable temp dir ─────────────────────────────────────────────────────────
TMP = tempfile.gettempdir()

# ── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SuperLiving | Ad Generator",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stApp { background: linear-gradient(135deg, #071a0f 0%, #0d2b1a 100%); }
    .brand-header {
        background: linear-gradient(90deg, #1a7a3c, #25a85a);
        padding: 1.5rem 2rem; border-radius: 16px; margin-bottom: 2rem;
        display: flex; align-items: center; gap: 1rem;
        box-shadow: 0 4px 24px rgba(26,122,60,0.35);
    }
    .brand-header h1 { color: #ffffff; margin: 0; font-size: 1.8rem; font-weight: 700; }
    .brand-header p  { color: rgba(255,255,255,0.85); margin: 0.2rem 0 0 0; font-size: 0.95rem; }
    .section-card {
        background: rgba(255,255,255,0.04); border: 1px solid rgba(37,168,90,0.18);
        border-radius: 14px; padding: 1.5rem; margin-bottom: 1.5rem;
    }
    .section-title {
        font-size: 1rem; font-weight: 600; color: #3ddc75; margin-bottom: 0.8rem;
        text-transform: uppercase; letter-spacing: 0.05em;
    }
    .badge-optional {
        display: inline-block; background: rgba(61,220,117,0.1); color: #3ddc75;
        border: 1px solid rgba(61,220,117,0.25); padding: 2px 10px; border-radius: 20px;
        font-size: 0.75rem; font-weight: 500; margin-left: 0.5rem; vertical-align: middle;
    }
    .info-box {
        background: rgba(37,168,90,0.08); border: 1px solid rgba(37,168,90,0.25);
        border-radius: 10px; padding: 0.8rem 1rem; font-size: 0.83rem; color: #7ecfa0;
        margin-bottom: 0.8rem;
    }
    .stButton > button {
        width: 100%; background: linear-gradient(90deg, #1a7a3c, #25a85a) !important;
        color: #ffffff !important; font-weight: 700 !important; font-size: 1.05rem !important;
        padding: 0.75rem 2rem !important; border: none !important; border-radius: 12px !important;
        letter-spacing: 0.02em !important;
    }
    .stButton > button:hover { opacity: 0.88; transform: translateY(-1px); }
    .stTextArea textarea, .stTextInput input {
        background: rgba(255,255,255,0.05) !important; border: 1px solid rgba(37,168,90,0.2) !important;
        border-radius: 10px !important; color: #e8f5ee !important;
    }
    label { color: #a8d4b8 !important; }
    h3 { color: #e8f5ee !important; }
    p { color: #7ecfa0; }
    .stSelectbox > div > div { color: #e8f5ee !important; }
    .stCheckbox > label > span { color: #a8d4b8 !important; }
</style>
""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="brand-header">
    <div style="font-size:2.5rem">🎬</div>
    <div>
        <h1>SuperLiving — Ad Generator: Demo</h1>
        <p>Transform your scripts into high-impact video ads for Tier 3 &amp; 4 India · Powered by AI</p>
    </div>
</div>
""", unsafe_allow_html=True)

# ── API Key (hardcoded — internal tool) ───────────────────────────────────────
API_KEY="AIzaSyANcnk-hn3ixX_Sju-gmwnVtWlW3bcWVSs"


# ── Layout ────────────────────────────────────────────────────────────────────
left, right = st.columns([3, 2], gap="large")

with left:
    # ── Script ────────────────────────────────────────────────────────────────
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📄 Ad Script</div>', unsafe_allow_html=True)
    script = st.text_area(
        "script_input", height=300, label_visibility="collapsed",
        key="script_input",
        placeholder=(
            "Paste the full ad script here — scene descriptions, dialogues, "
            "character details, CTAs...\n\n"
            "Example:\nOpening: Show cuts of women in a testimonial-type setting.\n"
            "Woman 1 (Rural, Age 38, kurti): Sirf 2 mahino mai mera 5 kilo weight loss hua..."
        ),
    )
    st.markdown('</div>', unsafe_allow_html=True)

    # ── Additional Instructions + Reference Images ─────────────────────────────
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-title">✨ Additional Instructions '
        '<span class="badge-optional">Optional</span></div>', unsafe_allow_html=True,
    )
    extra_prompt = st.text_area(
        "extra_instructions", height=100, label_visibility="collapsed",
        key="extra_instructions",
        placeholder=(
            "E.g. 'Warm golden-hour lighting throughout', "
            "'Handheld documentary feel', "
            "'Show Hindi subtitles on screen', "
            "'Fast cuts in opening, slow emotional pace in middle'..."
        ),
    )

    # ── Reference images for additional instructions ───────────────────────────
    st.markdown(
        '<p style="font-size:0.83rem;color:#777;margin:0.6rem 0 0.3rem 0;">'
        '📎 <b>Reference images</b> <span style="color:#555">(optional)</span> — '
        'upload mood boards, location references, product shots, etc. '
        'Gemini will use these as visual context when writing the scene prompts.'
        '</p>', unsafe_allow_html=True,
    )
    extra_images = st.file_uploader(
        "reference_images",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        label_visibility="collapsed",
        key="extra_ref_images",
    )
    if extra_images:
        img_cols = st.columns(min(len(extra_images), 4))
        for col, img in zip(img_cols, extra_images):
            with col:
                st.image(img, use_container_width=True)
        st.caption(f"{len(extra_images)} reference image(s) will be passed to Gemini as visual context.")
    st.markdown('</div>', unsafe_allow_html=True)

with right:
    # ── Character Photos ───────────────────────────────────────────────────────
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-title">👤 Character Photos '
        '<span class="badge-optional">Optional</span></div>', unsafe_allow_html=True,
    )
    st.markdown(
        '<p style="font-size:0.83rem;color:#777;margin-bottom:0.8rem;">'
        'Upload one clear photo per character. The same original photo is sent as the I2V '
        'reference for clip 1. For clips 2+, the exact last frame of the previous clip is used '
        'as the I2V starting image — creating a pixel-perfect match-cut for seamless continuity.'
        '</p>', unsafe_allow_html=True,
    )
    use_photos = st.checkbox("Add character reference photos", value=False, key="use_photos")
    characters = []
    if use_photos:
        num_chars = st.number_input("How many characters have photos?", min_value=1, max_value=8, value=2, step=1, key="num_chars")
        for i in range(int(num_chars)):
            st.markdown(f"**Character {i+1}**")
            c1, c2 = st.columns([2, 3])
            with c1:
                char_name = st.text_input("Name / Role", key=f"char_name_{i}", placeholder="e.g. Woman 1")
            with c2:
                char_photo = st.file_uploader("Photo", type=["jpg","jpeg","png","webp"],
                                               key=f"char_photo_{i}", label_visibility="collapsed")
            if char_photo:
                st.image(char_photo, width=90)
            characters.append({"name": char_name, "photo": char_photo})
    st.markdown('</div>', unsafe_allow_html=True)

    # ── Video Settings ─────────────────────────────────────────────────────────
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">⚙️ Video Settings</div>', unsafe_allow_html=True)

    DURATION_OPTIONS = {
        "8s  (1 clip  — fastest)":   {"clips": 1,  "label": "8s"},
        "30s (4 clips)":             {"clips": 4,  "label": "30s"},
        "45s (6 clips)":             {"clips": 6,  "label": "45s"},
        "60s (8 clips)":             {"clips": 8,  "label": "60s"},
    }
    duration_choice = st.selectbox(
        "Target Duration",
        list(DURATION_OPTIONS.keys()),
        index=2,
        help="Max 8s per clip. Longer ads are built by chaining extensions automatically.",
        key="duration_choice",
    )
    num_clips = DURATION_OPTIONS[duration_choice]["clips"]
    duration_label = DURATION_OPTIONS[duration_choice]["label"]

    st.markdown(
        f'<div class="info-box">⚙️ <b>Max 8 seconds per clip.</b> '
        f'Your {duration_label} ad will be built from <b>{num_clips} clip{"s" if num_clips>1 else ""}</b>. '
        f'Clips 2+ are seamless video extensions of the previous clip — '
        f'no cold starts, no continuity breaks.</div>',
        unsafe_allow_html=True,
    )

    veo_model = st.selectbox(
        "Generation Model",
        ["veo-3.1-generate-preview", "veo-3.0-fast-generate-001"],
        index=0,
        help="Standard = best quality + native audio. Fast = quicker generation, no audio.",
        key="veo_model",
        format_func=lambda x: "Standard Quality (Audio)" if x == "veo-3.1-generate-preview" else "Fast Generation (No Audio)",
    )
    aspect_ratio = st.selectbox(
        "Aspect Ratio",
        ["9:16 (Reels / Shorts)", "16:9 (YouTube / Landscape)"],
        index=0,
        key="aspect_ratio",
    )
    language_note = st.checkbox("Include Hindi dialogue delivery notes", value=True, key="language_note")
    st.markdown('</div>', unsafe_allow_html=True)

# ── Generate Button ───────────────────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
_, gen_col, _ = st.columns([1, 2, 1])
with gen_col:
    generate_btn = st.button("🎬  Generate Ad", use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def poll_operation(video_client, operation, label: str):
    poll_ph = st.empty()
    elapsed = 0
    poll_interval = 20
    max_wait = 720
    while not operation.done:
        mins, secs = divmod(elapsed, 60)
        t = f"{mins}m {secs}s" if mins else f"{secs}s"
        poll_ph.info(f"⏳ {label} — {t} elapsed (typical: 3–6 min per clip)")
        time.sleep(poll_interval)
        elapsed += poll_interval
        operation = video_client.operations.get(operation)
        if elapsed >= max_wait:
            poll_ph.error("⏰ Timed out. Try again or use fewer clips.")
            return None
    poll_ph.empty()
    return operation


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
                f"Key 1 — 'appearance': describe ONLY face and body (NOT clothing).\n"
                f"Include: skin tone (exact), face shape, eye shape+color+spacing, brow shape, "
                f"nose shape, lip fullness, jawline, distinctive marks (moles/dimples/wrinkles "
                f"with exact location), hair (color/texture/length/style/parting/grey), "
                f"age range (e.g. '35-40'), build. "
                f"One dense paragraph starting with '{name} is a [age]...'.\n\n"
                f"Key 2 — 'outfit': describe ONLY what they are wearing.\n"
                f"Every garment, color, fabric, pattern, fit. Be exact — this is locked forever.\n"
                f"One sentence starting with 'Wearing...'.\n\n"
                f"Return ONLY valid JSON: {{\"appearance\": \"...\", \"outfit\": \"...\"}}\n"
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

ABOUT SUPERLIVING: Indian health app, target = women 25-60 in Tier 3 & 4 India.
Tone = authentic, real, emotional. Real homes, real faces, warm earthy tones.

{char_consistency_rule}

SCRIPT COVERAGE — CRITICAL:
You MUST cover the ENTIRE script from start to finish across all {num_clips} clips.
- Divide the script content EVENLY across all clips — no clip should be empty or filler.
- The LAST CLIP must contain the script's conclusion, CTA, or final message.
- If the script has a brand name, tagline, or call-to-action, it MUST appear in the final clip.
- Do NOT end abruptly — the final clip must feel like a satisfying conclusion.
- Before generating, mentally map: which script portions go into which clips.

PACING & ENGAGEMENT — NO DEAD AIR:
Every clip MUST have EITHER dialogue OR significant visible action. Ads must be gripping.
- MINIMUM per clip: at least 8 words of dialogue OR a clear dramatic action beat.
- NO silent contemplation clips — if a character is thinking, they should voice it.
- NO filler clips with just "character smiles" — always pair with dialogue or movement.
- If the script has less dialogue, add contextual voiceover narration to fill gaps.
- Prefer dialogue-heavy clips — talking heads are engaging for Indian audiences.

CONTINUITY RULES:
- Every prompt except clip 1 MUST begin with a CONTINUING FROM: block.
  At generation time this is auto-replaced with a Gemini Vision description of actual
  rendered frames — so write a detailed scripted version as placeholder:
  "CONTINUING FROM: [exact final frame — character position, expression, room, lighting,
   camera angle/distance, what was just said or done, emotional state, BGM state]"
- The new clip's action must flow ORGANICALLY from that moment — mid-sentence dialogue
  can continue, movement can carry over, emotional arc continues unbroken.
- BGM described identically across all clips (same song, note if it swells or fades)
- End every prompt with: "LAST FRAME: [exact position, expression, camera, framing]"

DIALOGUE FORMAT — MANDATORY:
Every line of dialogue must use speaker-colon format:
  चरित्र का नाम: "बोला हुआ संवाद"
Examples:
  माँ: "बेटा, आज बहुत थकान हो गई।"
  बेटी: "माँ, अब सब ठीक हो जाएगा।"
  वॉयसओवर: "सुपरलिविंग — हर कदम पर आपके साथ।"
NEVER write dialogue without the speaker name and colon prefix.
Multiple speakers in one clip = list each on a new line with their name.

DIALOGUE LENGTH — BALANCED FOR LIP-SYNC:
Each 8-second clip should have 8-20 Hindi words of spoken dialogue.
- MINIMUM: 8 words per clip (keeps the ad engaging, no dead air)
- MAXIMUM: 20 words per clip (beyond this, lip-sync becomes rushed)
- IDEAL: 10-15 words per clip (natural speaking pace)
- Split longer sentences across clips if needed — mid-sentence cuts are fine.
- If a clip truly needs silence (rare emotional beat), limit to MAX 1 such clip per ad.
- Voiceover can supplement character dialogue to hit the minimum.

FINAL CLIP — MANDATORY CONCLUSION:
The last clip (clip {num_clips}) MUST contain:
1. The script's concluding statement or final dialogue
2. Brand mention: "सुपरलिविंग" spoken or shown
3. A clear call-to-action if present in script (e.g., "आज ही डाउनलोड करें")
4. Satisfying visual closure — character smiling at camera, product shot, or logo moment
5. Do NOT leave any script content unused — the last clip wraps up everything

CLIP PROMPT STRUCTURE (follow every time):
1. OUTFIT LINE (mandatory): "[Name] is wearing [locked outfit] and [locked appearance]."
2. LOCATION: specify exact room/place
3. Action & emotion arc for these 8 seconds
4. Dialogue: each line as चरित्र: "संवाद" (8-20 words total per clip)
5. Audio: BGM (consistent across clips), ambient sounds
6. CAMERA: continuous take, describe angle/distance/movement
7. LIGHTING: copy exact description from Clip 1 to maintain color consistency
8. LAST FRAME: [exact final frame description for next clip continuity]

AUDIO-VISUAL SYNC (add to every prompt that has dialogue):
"Audio-visual sync: match lip movements precisely to spoken dialogue."

CONTENT SAFETY (Veo silently rejects these):
- No diseases, conditions, symptoms, medicines, BP, sugar, diabetes, weight loss, pain
- No doctors, prescriptions, treatments
- Reframe as: confidence, energy, wellness, happiness, lifestyle

VISUAL FORMAT PROHIBITIONS (add to every prompt):
- "No cinematic letterbox bars. No black bars. Full {ar} frame edge to edge."
- "No burned-in subtitles. No text overlays. No lower thirds. No captions. No watermarks."

{"Dialogue: note tone e.g. 'warmly, looking at camera'" if language_note else ""}
Each prompt: 200–270 words. More detail = better continuity.
Aspect ratio: {ar}

OUTPUT: valid JSON only:
{{
  "clips": [
    {{"clip": 1, "scene_summary": "brief label", "last_frame": "...", "prompt": "..."}},
    {{"clip": 2, "scene_summary": "brief label", "last_frame": "...", "prompt": "CONTINUING FROM: [...]. ..."}},
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
            temperature=0.7,
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


def _get_ffmpeg() -> str:
    """Return path to ffmpeg binary or raise."""
    import shutil as _shutil
    ffmpeg_bin = _shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        try:
            import imageio_ffmpeg
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    if ffmpeg_bin is None:
        raise RuntimeError("ffmpeg not found.")
    return ffmpeg_bin


def extract_last_n_frames(video_path: str, n: int = 10) -> list:
    """
    Extract the last N frames of an MP4 as a list of JPEG bytes.
    Samples evenly across the last 2 seconds of the clip.
    Returns list of bytes objects, ordered earliest → latest.
    """
    ffmpeg_bin = _get_ffmpeg()
    frames = []
    # Sample n frames evenly across the last 2s
    for k in range(n):
        # offset from end: from -2.0s to -0.1s in n steps
        t_from_end = 2.0 - (k / max(n - 1, 1)) * 1.9   # 2.0 → 0.1
        out_path = video_path.replace(".mp4", f"_frame_{k:02d}.jpg")
        r = subprocess.run(
            [ffmpeg_bin, "-y", "-sseof", f"-{t_from_end:.3f}",
             "-i", video_path, "-vframes", "1", "-q:v", "2", out_path],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and os.path.exists(out_path):
            with open(out_path, "rb") as f:
                frames.append(f.read())
    if not frames:
        raise RuntimeError(f"Could not extract any frames from {video_path}")
    return frames


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
        f"- Room: exact location, which objects are visible and where\n"
        f"- Camera: angle (eye-level/low/high), distance (close-up/medium/wide), "
        f"  any movement that was happening (pan/tilt/static)\n"
        f"- Lighting: quality (soft/harsh), direction (from left/right/behind), "
        f"  color temperature (warm/cool/golden)\n"
        f"- Audio state: was anyone speaking, what was the BGM doing\n"
        f"- Emotional momentum: what feeling is in the air as this clip ends\n\n"
        f"Format: start directly with 'CONTINUING FROM:' — no preamble.\n"
        f"Keep it under 120 words. Be factual and specific, not poetic."
    )))

    response = gemini_client.models.generate_content(
        model="gemini-2.0-flash",
        contents=contents,
    )
    # Safely handle None response
    if response is None or response.text is None:
        return "CONTINUING FROM: [previous frame — details unavailable]"
    return response.text.strip()


def extract_last_frame(video_path: str) -> bytes:
    """
    Extract the absolute last frame of an MP4 as JPEG bytes.

    WHY A SINGLE LAST FRAME (not a collage):
    Veo's I2V treats the input image as literal frame 0 of the new clip.
    A multi-frame collage causes grid-like artifacts and hallucinations because
    the diffusion model tries to "continue" from a composite image that never
    existed as a real video frame. Using the exact last frame gives Veo a
    pixel-perfect match-cut starting point — the new clip begins exactly where
    the previous clip ended, creating the illusion of a single unbroken take.
    """
    ffmpeg_bin = _get_ffmpeg()
    out_path = video_path.replace(".mp4", "_last_frame.jpg")
    # -sseof -0.04 seeks to ~1 frame before EOF (at 24fps ≈ 0.042s)
    r = subprocess.run(
        [ffmpeg_bin, "-y", "-sseof", "-0.04",
         "-i", video_path, "-vframes", "1", "-q:v", "2", out_path],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not os.path.exists(out_path):
        # Fallback: try seeking to -0.1s from end
        r = subprocess.run(
            [ffmpeg_bin, "-y", "-sseof", "-0.1",
             "-i", video_path, "-vframes", "1", "-q:v", "2", out_path],
            capture_output=True, text=True,
        )
    if r.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"Could not extract last frame from {video_path}")
    with open(out_path, "rb") as f:
        return f.read()


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
    st.write(f"  🎞️ Extracting last {n_frames} frames from clip {clip_num - 1} for analysis...")
    frames = extract_last_n_frames(prev_video_path, n=n_frames)
    st.write(f"  ✅ {len(frames)} frames extracted for Gemini analysis")

    st.write(f"  🧠 Gemini analysing frames → generating CONTINUING FROM...")
    continuing_from = build_continuing_from(
        gemini_client, frames, clip_num, next_scene_summary
    )
    with st.expander(f"📋 Auto-generated CONTINUING FROM: — Clip {clip_num}"):
        st.write(continuing_from)

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
    # Veo treats this as literal frame 0. Using the exact last frame of the
    # previous clip creates a pixel-perfect match-cut — no grid artifacts,
    # no hallucinations, just seamless continuation.
    st.write(f"  🖼️ Extracting absolute last frame for I2V...")
    last_frame_bytes = extract_last_frame(prev_video_path)
    st.write(f"  ✅ Last frame extracted ({len(last_frame_bytes)//1024} KB) → I2V starting image")
    with st.expander(f"🖼️ Last frame — Clip {clip_num} I2V reference"):
        st.image(last_frame_bytes, caption="Exact last frame of previous clip → I2V frame 0")

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


class RaiCelebrityError(Exception):
    """Raised when Veo rejects the I2V input image due to celebrity detection."""
    pass

class RaiContentError(Exception):
    """Raised when Veo rejects the prompt due to content policy."""
    pass

def extract_generated_video(operation, clip_num: int):
    """
    Pull the generated video object from a completed operation.
    Raises typed exceptions for RAI blocks so the caller can handle them:
      - RaiCelebrityError: I2V input image flagged as celebrity → retry without image
      - RaiContentError:   prompt blocked → trigger rephrase and retry
    """
    with st.expander(f"🔍 Debug — Clip {clip_num} raw response"):
        st.text(str(operation)[:3000])

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
                st.warning(
                    f"🚫 Clip {clip_num}: I2V image flagged as celebrity likeness.\n"
                    f"Reason: {reasons_str}\n"
                    f"→ Will retry without last-frame image (text-only)."
                )
                raise RaiCelebrityError(reasons_str)
            else:
                st.warning(
                    f"🚫 Clip {clip_num}: RAI content filter triggered.\n"
                    f"Reason: {reasons_str}\n"
                    f"→ Will rephrase prompt and retry."
                )
                raise RaiContentError(reasons_str)

    generated = None
    if hasattr(operation, "response") and operation.response:
        generated = getattr(operation.response, "generated_videos", None)
    if not generated and hasattr(operation, "result") and operation.result:
        generated = getattr(operation.result, "generated_videos", None)

    if not generated:
        st.error(
            f"Clip {clip_num} returned empty — likely a content policy block. "
            "Check the debug expander above."
        )
        return None

    return generated[0].video


def download_video(uri: str, api_key: str) -> bytes:
    import urllib.request
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
            st.warning("⚠️ Rephrase returned empty — using original prompt")
            return original_prompt
        return response.text.strip()
    except Exception as e:
        st.warning(f"⚠️ Rephrase failed ({e}) — using original prompt")
        return original_prompt


def stitch_clips(clip_paths: list, output_path: str) -> bool:
    """
    Stitch AI-generated clips into one seamless video with zero audio pops,
    zero A/V desync, and zero timestamp drift.

    ─── THE PROBLEM ───
    Veo-generated clips have audio streams that are a fraction of a second
    longer or shorter than the video stream. When naïvely concatenated:
      - Audio pops/clicks at every cut boundary (partial AAC frames)
      - Cumulative A/V drift (each clip adds ±50ms of misalignment)
      - Lip-sync breaks down after 2-3 clips

    ─── THE FIX: 3-STAGE NORMALIZATION ───

    Stage 1 — Video: force exact 24fps, yuv420p, even resolution, H.264.
              This gives every clip identical GOP structure and timebase.

    Stage 2 — Audio sync (THE CRITICAL PART):
      • aresample=async=1  →  stretches/squeezes audio timestamps to match
                               the video clock. Eliminates sub-frame drift.
      • apad                →  pads silence at the end if audio is shorter
                               than video (prevents abrupt cutoff).
      • -shortest           →  truncates the padded audio exactly when the
                               video stream ends (clean cut, no overhang).
      Together these three guarantee: audio duration == video duration,
      sample-accurately, for every single clip.

      For clips WITHOUT audio: generate anullsrc silence trimmed to the
      exact video duration (parsed from ffmpeg -i stderr, no ffprobe).

    Stage 3 — Concat demuxer with -c copy. Because every clip now has
              identical codec params AND identical A/V durations, stream-copy
              concat is frame-perfect with zero re-encoding artifacts.

    NO FFPROBE DEPENDENCY — duration parsed from `ffmpeg -i` stderr via regex.
    """
    import re as _re

    # ── Locate ffmpeg binary ──────────────────────────────────────────────────
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        try:
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    if not ffmpeg_bin:
        st.error("❌ ffmpeg not found — cannot stitch clips.")
        return False

    # ── Helper: parse duration from ffmpeg -i stderr (no ffprobe) ─────────────
    def probe_duration(path: str) -> float:
        r = subprocess.run(
            [ffmpeg_bin, "-i", path],
            capture_output=True, text=True,
        )
        m = _re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        st.warning(f"⚠️ Could not parse duration for {os.path.basename(path)} — assuming 7.7s")
        return 7.7

    # ── Helper: check if clip contains an audio stream ────────────────────────
    def has_audio_stream(path: str) -> bool:
        r = subprocess.run(
            [ffmpeg_bin, "-i", path],
            capture_output=True, text=True,
        )
        return "Audio:" in r.stderr

    try:
        # ══════════════════════════════════════════════════════════════════════
        # STAGE 1+2: Normalize every clip — video + audio sync
        #
        # Goal: every output file has EXACTLY matching video and audio
        # durations, identical codecs, and clean stream boundaries.
        # ══════════════════════════════════════════════════════════════════════
        normalized = []

        for i, p in enumerate(clip_paths):
            norm_path = os.path.join(TMP, f"norm_{i:02d}.mp4")
            clip_has_audio = has_audio_stream(p)

            if clip_has_audio:
                # ── HAS AUDIO: aresample→apad→-shortest pipeline ─────────
                #
                # aresample=async=1:
                #   Resamples audio so its timestamps exactly match the video
                #   clock. If audio is 7.68s but video is 7.70s, async=1
                #   stretches/inserts silence samples to fill the gap.
                #   This kills sub-frame drift that causes cumulative desync.
                #
                # apad:
                #   Pads the audio with silence PAST the video end — this
                #   guarantees audio is never shorter than video (which would
                #   cause a pop/click at the cut boundary).
                #
                # -shortest:
                #   Terminates the output when the SHORTEST stream (video)
                #   ends. Since apad made audio infinite, -shortest cleanly
                #   cuts it at exactly the video duration. No overhang.
                #
                # Net result: audio_duration == video_duration, sample-perfect.
                st.write(f"  📎 Clip {i+1}: normalizing video + audio (aresample→apad→shortest)...")
                r = subprocess.run(
                    [ffmpeg_bin, "-y", "-i", p,
                     # ── Video filters ──
                     "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=24,format=yuv420p",
                     "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                     "-pix_fmt", "yuv420p",
                     # ── Audio filters (the critical sync chain) ──
                     "-af", "aresample=async=1,apad",
                     "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k",
                     # ── Trim audio to exact video length ──
                     "-shortest",
                     norm_path],
                    capture_output=True, text=True,
                )
                if r.returncode != 0:
                    st.warning(f"  ⚠️ Clip {i+1}: aresample pipeline failed, trying basic normalize...")
                    with st.expander(f"🔧 Clip {i+1} aresample error", expanded=False):
                        st.code(r.stderr[-800:])
                    # Fallback: basic normalize without aresample (still better than raw)
                    r = subprocess.run(
                        [ffmpeg_bin, "-y", "-i", p,
                         "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=24,format=yuv420p",
                         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                         "-pix_fmt", "yuv420p",
                         "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k",
                         "-shortest",
                         norm_path],
                        capture_output=True, text=True,
                    )
                    if r.returncode != 0:
                        raise RuntimeError(
                            f"Normalize clip {i+1} failed (both pipelines):\n{r.stderr[-500:]}"
                        )

            else:
                # ── NO AUDIO: generate silence trimmed to exact video duration ─
                #
                # We probe the video-only duration first, then generate a
                # silent audio track of exactly that length with -t.
                # This is more precise than -shortest with anullsrc (which
                # can leave a trailing partial AAC frame).
                vid_dur = probe_duration(p)
                st.write(f"  🔇 Clip {i+1}: no audio — generating {vid_dur:.3f}s silence track...")
                r = subprocess.run(
                    [ffmpeg_bin, "-y",
                     "-i", p,
                     "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={vid_dur:.4f}",
                     # ── Video ──
                     "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=24,format=yuv420p",
                     "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                     "-pix_fmt", "yuv420p",
                     # ── Map video from input 0, audio from input 1 ──
                     "-map", "0:v:0", "-map", "1:a:0",
                     "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k",
                     # ── Hard trim to video duration (belt + suspenders) ──
                     "-t", f"{vid_dur:.4f}",
                     norm_path],
                    capture_output=True, text=True,
                )
                if r.returncode != 0:
                    raise RuntimeError(
                        f"Normalize+silence clip {i+1} failed:\n{r.stderr[-500:]}"
                    )

            # Log the result
            dur = probe_duration(norm_path)
            normalized.append(norm_path)
            sz = os.path.getsize(norm_path) // 1024
            st.write(f"  ✅ Clip {i+1}: {dur:.2f}s normalized ({sz} KB)")

        # ── Single clip — just copy, no concat needed ─────────────────────────
        if len(normalized) == 1:
            shutil.copy(normalized[0], output_path)
            st.write("  ✅ Single clip — no stitching needed")
            return True

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 3: Concat demuxer — stream-copy (primary), re-encode (fallback)
        #
        # All clips now have:
        #   ✓ Identical video codec (H.264 main, yuv420p, 24fps)
        #   ✓ Identical audio codec (AAC, 44100Hz, stereo, 128kbps)
        #   ✓ audio_duration == video_duration (sample-perfect)
        #
        # Stream-copy concat should be seamless. Re-encode fallback exists
        # only for edge cases (profile/level mismatches across clips).
        # ══════════════════════════════════════════════════════════════════════

        # Write concat list with absolute paths (cross-platform safe)
        list_file = os.path.join(TMP, "veo_concat_list.txt")
        with open(list_file, "w") as f:
            for p in normalized:
                safe_path = os.path.abspath(p).replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

        # ── 3a: Stream-copy concat (fast, zero quality loss) ──────────────────
        r_copy = subprocess.run(
            [ffmpeg_bin, "-y", "-f", "concat", "-safe", "0",
             "-i", list_file,
             "-c", "copy",
             "-movflags", "+faststart",
             output_path],
            capture_output=True, text=True,
        )

        if (r_copy.returncode == 0
                and os.path.exists(output_path)
                and os.path.getsize(output_path) > 100_000):
            sz = os.path.getsize(output_path) // (1024 * 1024)
            final_dur = probe_duration(output_path)
            st.write(
                f"  ✅ Final video: {sz} MB, {final_dur:.2f}s "
                f"(stream-copy concat, A/V sync locked)"
            )
            return True

        # ── 3b: Re-encode concat (fallback) ──────────────────────────────────
        st.warning("⚠️ Stream-copy concat failed — falling back to re-encode concat.")
        with st.expander("🔧 Stream-copy error (debug)", expanded=False):
            st.code(r_copy.stderr[-2000:])

        r_reencode = subprocess.run(
            [ffmpeg_bin, "-y", "-f", "concat", "-safe", "0",
             "-i", list_file,
             "-vf", "fps=24,format=yuv420p",
             "-c:v", "libx264", "-preset", "fast", "-crf", "18",
             "-pix_fmt", "yuv420p",
             "-af", "aresample=async=1",
             "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k",
             "-movflags", "+faststart",
             output_path],
            capture_output=True, text=True,
        )

        if (r_reencode.returncode == 0
                and os.path.exists(output_path)
                and os.path.getsize(output_path) > 100_000):
            sz = os.path.getsize(output_path) // (1024 * 1024)
            final_dur = probe_duration(output_path)
            st.write(
                f"  ✅ Final video: {sz} MB, {final_dur:.2f}s "
                f"(re-encode concat fallback, A/V sync locked)"
            )
            return True

        st.error(
            f"❌ All stitching methods failed.\n"
            f"Stream-copy stderr:\n{r_copy.stderr[-400:]}\n\n"
            f"Re-encode stderr:\n{r_reencode.stderr[-400:]}"
        )
        return False

    except Exception as e:
        st.error(f"❌ Stitch error: {e}")
        st.code(traceback.format_exc())
        return False



# ══════════════════════════════════════════════════════════════════════════════
# MAIN GENERATION FLOW
# ══════════════════════════════════════════════════════════════════════════════

if generate_btn:
    if not API_KEY:
        st.error("⚠️ Please enter and save your Google API Key first.")
        st.stop()
    if not script.strip():
        st.error("⚠️ Please paste your ad script before generating.")
        st.stop()

    gemini_client = genai.Client(api_key=API_KEY)
    video_client  = genai.Client(api_key=API_KEY, http_options={"api_version": "v1alpha"})

    ar_map = {"9:16 (Reels / Shorts)": "9:16", "16:9 (YouTube / Landscape)": "16:9"}
    ar = ar_map.get(aspect_ratio, "9:16")

    st.markdown("---")
    st.markdown("### 🔄 Generation Pipeline")

    # ── STEP 0: Read extra reference images ───────────────────────────────────
    extra_image_parts = []
    if extra_images:
        for img_file in extra_images:
            img_bytes = img_file.read()
            mime = img_file.type or "image/jpeg"
            extra_image_parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))
        st.info(f"📎 {len(extra_image_parts)} reference image(s) will be used as visual context.")

    # ── STEP 1: Load & analyse character photos ───────────────────────────────
    char_photos_raw = []   # [(name, bytes, mime_type), ...]
    photo_analyses  = {}   # {name: {"appearance": str, "outfit": str}}

    if use_photos and characters:
        with st.status("📸 Loading & analysing character photos...", expanded=True) as status:
            for char in characters:
                if char["photo"] and char["name"].strip():
                    name       = char["name"].strip()
                    st.write(f"Loading: **{name}**")
                    photo_bytes = char["photo"].read()
                    mime_type   = char["photo"].type or "image/jpeg"
                    char_photos_raw.append((name, photo_bytes, mime_type))

                    st.write(f"🔍 Extracting locked face + outfit: **{name}**...")
                    try:
                        analysis = analyze_character_photo(gemini_client, name, photo_bytes, mime_type)
                        photo_analyses[name] = analysis
                        st.write(f"✅ **{name}** locked")
                        with st.expander(f"📋 Locked profile: {name}"):
                            st.markdown(f"**Outfit (locked):** {analysis['outfit']}")
                            st.markdown(f"**Appearance:** {analysis['appearance']}")
                    except Exception as e:
                        st.warning(f"Could not analyse {name}: {e}. Image will still be sent.")

            status.update(label=f"✅ {len(char_photos_raw)} photo(s) loaded & analysed!", state="complete")

        if char_photos_raw:
            st.info(
                "🔒 **Continuity strategy:**  \n"
                "① Clip 1 — original character photo as I2V starting frame  \n"
                "② Clips 2+ — exact last frame of previous clip as I2V (seamless match-cut)  \n"
                "③ ALL clips — Gemini Vision CONTINUING FROM + locked outfit/appearance text"
            )

    # ── STEP 2: Character sheet (no-photos path only) ─────────────────────────
    character_sheet = ""
    if not char_photos_raw:
        with st.status("🎭 Building character consistency sheet...", expanded=True) as status:
            try:
                character_sheet = build_character_sheet(gemini_client, script)
                status.update(label="✅ Character sheet locked!", state="complete")
            except Exception as e:
                status.update(label="❌ Failed", state="error")
                st.error(f"Gemini error: {e}")
                st.stop()
        with st.expander("👥 View Character Sheet", expanded=False):
            st.text(character_sheet)

    # ── STEP 3: Build clip prompts ────────────────────────────────────────────
    with st.status(f"🧠 Building {num_clips} scene prompt(s)...", expanded=True) as status:
        try:
            clip_data = build_clip_prompts(
                client=gemini_client,
                script=script,
                extra_prompt=extra_prompt,
                extra_image_parts=extra_image_parts,
                character_sheet=character_sheet,
                photo_analyses=photo_analyses,
                aspect_ratio=aspect_ratio,
                num_clips=num_clips,
                language_note=language_note,
                has_photos=bool(char_photos_raw),
            )
            for c in clip_data:
                st.write(f"Clip {c['clip']}: *{c['scene_summary']}*")
            status.update(label=f"✅ {num_clips} prompt(s) ready!", state="complete")
        except Exception as e:
            status.update(label="❌ Failed", state="error")
            st.error(f"Gemini error: {e}")
            st.stop()

    # ── Save generated data to session_state so edit UI can access it ───────
    st.session_state["_sl_clip_data"]        = clip_data
    st.session_state["_sl_character_sheet"]  = character_sheet
    st.session_state["_sl_photo_analyses"]   = photo_analyses
    st.session_state["_sl_char_photos_raw"]  = char_photos_raw
    st.session_state["_sl_extra_image_parts"] = extra_image_parts
    st.session_state["_sl_ar"]               = ar
    st.session_state["_sl_num_clips"]        = num_clips
    st.session_state["_sl_veo_model"]        = veo_model
    st.session_state["_sl_prompts_ready"]    = True
    # Clear any previous video result
    st.session_state.pop("_sl_final_path", None)
    st.session_state.pop("_sl_clip_paths",  None)
    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# EDIT & CONFIRM PHASE
# Shown after prompts are generated, before video generation starts.
#  ══════════════════════════════════════════════════════════════════════════════

if (st.session_state.get("_sl_prompts_ready")
    and not st.session_state.get("_sl_final_path")
    and not st.session_state.get("_sl_regen_trigger")
    and not st.session_state.get("_sl_regen_running")):
    clip_data        = st.session_state["_sl_clip_data"]
    character_sheet  = st.session_state["_sl_character_sheet"]
    photo_analyses   = st.session_state["_sl_photo_analyses"]
    char_photos_raw  = st.session_state["_sl_char_photos_raw"]
    extra_image_parts = st.session_state["_sl_extra_image_parts"]
    ar               = st.session_state["_sl_ar"]
    num_clips        = st.session_state["_sl_num_clips"]
    veo_model        = st.session_state["_sl_veo_model"]

    st.markdown("---")
    st.markdown("### ✏️ Review & Edit Before Generating")
    st.info(
        "Review the generated character sheet and clip prompts below. "
        "Edit anything you want — fix dialogue, adjust scenes, change descriptions. "
        "When you're happy, click **Confirm & Generate Video**."
    )

    # ── Edit character sheet (only shown on no-photos path) ───────────────────
    if character_sheet:
        st.markdown("#### 👥 Character Sheet")
        edited_character_sheet = st.text_area(
            "character_sheet_edit",
            value=character_sheet,
            height=250,
            label_visibility="collapsed",
            key="edit_char_sheet",
        )
    else:
        edited_character_sheet = ""

    # ── Edit per-clip prompts ─────────────────────────────────────────────────
    st.markdown("#### 🎬 Clip Prompts")
    st.caption("Each prompt is sent directly to the AI. Edit freely — outfit lines, dialogue, camera, anything.")

    edited_clip_data = []
    for c in clip_data:
        with st.expander(f"Clip {c['clip']} — {c['scene_summary']}", expanded=(c['clip'] == 1)):
            edited_summary = st.text_input(
                "Scene summary",
                value=c["scene_summary"],
                key=f"edit_summary_{c['clip']}",
            )
            edited_prompt = st.text_area(
                "Prompt",
                value=c["prompt"],
                height=350,
                key=f"edit_prompt_{c['clip']}",
                label_visibility="collapsed",
            )
            edited_clip_data.append({
                "clip":          c["clip"],
                "scene_summary": edited_summary,
                "last_frame":    c.get("last_frame", ""),
                "prompt":        edited_prompt,
            })

    # ── Download edited prompts ───────────────────────────────────────────────
    all_prompts_txt = "\n\n---\n\n".join(
        f"CLIP {c['clip']} — {c['scene_summary']}\n{c['prompt']}" for c in edited_clip_data
    )
    st.download_button(
        "⬇️ Download Prompts",
        data=all_prompts_txt,
        file_name="superliving_prompts.txt",
        mime="text/plain",
        key="dl_prompts_edit",
    )

    st.markdown("<br>", unsafe_allow_html=True)
    _, confirm_col, _ = st.columns([1, 2, 1])
    with confirm_col:
        confirm_btn = st.button(
            "✅  Confirm & Generate Video",
            use_container_width=True,
            key="confirm_generate",
            type="primary",
        )

    if confirm_btn:
        st.session_state["_sl_clip_data"]       = edited_clip_data
        st.session_state["_sl_character_sheet"] = edited_character_sheet
        st.session_state["_sl_confirmed"]       = True
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# VIDEO GENERATION PHASE (triggered after confirm)
# ══════════════════════════════════════════════════════════════════════════════

if (st.session_state.get("_sl_confirmed")
    and not st.session_state.get("_sl_final_path")
    and not st.session_state.get("_sl_regen_trigger")
    and not st.session_state.get("_sl_regen_running")):
    st.session_state["_sl_confirmed"] = False

    clip_data         = st.session_state["_sl_clip_data"]
    char_photos_raw   = st.session_state["_sl_char_photos_raw"]
    ar                = st.session_state["_sl_ar"]
    num_clips         = st.session_state["_sl_num_clips"]
    veo_model         = st.session_state["_sl_veo_model"]

    gemini_client = genai.Client(api_key=API_KEY)
    video_client  = genai.Client(api_key=API_KEY, http_options={"api_version": "v1alpha"})

    st.markdown("---")
    st.markdown("### 🔄 Generating Video...")

    # ── STEP 4: Generate clips ─────────────────────────────────────────────
    clip_paths  = []
    MAX_RETRIES = 3

    with st.status(f"🎬 Generating {num_clips} clip(s) with Veo...", expanded=True) as status:
        try:
            for i, clip in enumerate(clip_data):
                st.write(f"🎥 Clip {clip['clip']}/{num_clips}: *{clip['scene_summary']}*")
                current_prompt = clip["prompt"]
                operation      = None

                # ── Pre-sanitize before first attempt ─────────────────────────
                # Run every prompt through Gemini to strip Veo guardrail triggers
                # BEFORE sending — saves a full 3-6 min generation cycle per block.
                with st.spinner(f"🛡️ Pre-sanitizing clip {clip['clip']} for Veo guardrails..."):
                    try:
                        sanitized = sanitize_prompt_for_veo(
                            gemini_client, current_prompt, clip["clip"]
                        )
                        if sanitized and len(sanitized) > 100:
                            current_prompt = sanitized
                            with st.expander(f"🛡️ Sanitized prompt — Clip {clip['clip']}"):
                                st.code(current_prompt, language=None)
                        else:
                            st.warning(f"⚠️ Sanitizer returned empty — using original prompt")
                    except Exception as san_err:
                        st.warning(f"⚠️ Sanitizer failed ({san_err}) — using original prompt")

                for attempt in range(1, MAX_RETRIES + 1):
                    if attempt > 1:
                        st.warning(
                            f"⚠️ Clip {clip['clip']} blocked/failed (attempt {attempt-1}). "
                            "Rephrasing and retrying..."
                        )
                        with st.spinner(f"🔄 Rephrasing clip {clip['clip']}..."):
                            current_prompt = rephrase_blocked_prompt(
                                gemini_client, current_prompt, attempt
                            )
                        with st.expander(f"📝 Rephrased — Attempt {attempt}"):
                            st.code(current_prompt, language=None)

                    try:
                        if i == 0:
                            # ── Clip 1 (i=0): I2V from original character photo ──
                            if char_photos_raw:
                                ref_bytes, ref_mime, matched = get_clip_character_photo(
                                    current_prompt, char_photos_raw
                                )
                                st.write(f"📎 Clip 1: using **{matched}** original photo as I2V starting frame")
                                operation = generate_clip_from_image(
                                    video_client, veo_model, current_prompt, ar,
                                    clip["clip"], num_clips, ref_bytes, ref_mime,
                                )
                            else:
                                st.write("📝 Text-only generation (no character photo)")
                                operation = generate_clip_text_only(
                                    video_client, veo_model, current_prompt, ar,
                                    clip["clip"], num_clips,
                                )
                        else:
                            # ── Clips 2+: ALWAYS use last-frame I2V ──────────
                            # The last frame of the previous clip is always used
                            # as I2V starting image for seamless match-cuts.
                            # Gemini Vision CONTINUING FROM text provides scene
                            # and character context. We never force a solo portrait
                            # photo into a multi-character scene.
                            prev_path    = clip_paths[i - 1]
                            next_summary = clip_data[i]["scene_summary"] if i < len(clip_data) else ""
                            st.write(f"🎞️ Clip {clip['clip']} (last-frame I2V): continuing from clip {clip['clip']-1}'s last frame")
                            operation, current_prompt = generate_clip_with_frame_context(
                                video_client, gemini_client,
                                veo_model, current_prompt, ar,
                                clip["clip"], num_clips,
                                prev_path, next_summary,
                            )

                    except Exception as gen_err:
                        st.warning(
                            f"⚠️ Last-frame I2V failed: {gen_err}  \n"
                            "Falling back to character photo I2V..."
                        )
                        if char_photos_raw:
                            ref_bytes, ref_mime, matched = get_clip_character_photo(
                                current_prompt, char_photos_raw
                            )
                            operation = generate_clip_from_image(
                                video_client, veo_model, current_prompt, ar,
                                clip["clip"], num_clips, ref_bytes, ref_mime,
                            )
                        else:
                            operation = generate_clip_text_only(
                                video_client, veo_model, current_prompt, ar,
                                clip["clip"], num_clips,
                            )

                    if operation is None:
                        if attempt < MAX_RETRIES:
                            continue
                        st.error(f"❌ Clip {clip['clip']} timed out after {MAX_RETRIES} attempts.")
                        status.update(label=f"❌ Clip {clip['clip']} timed out", state="error")
                        st.stop()

                    try:
                        video_obj = extract_generated_video(operation, clip["clip"])
                    except RaiCelebrityError:
                        # The last frame contains a Veo-rendered face that RAI flags as
                        # celebrity likeness. We can NOT use text-only — that loses the
                        # visual face anchor and breaks continuity for all subsequent clips.
                        #
                        # CORRECT FALLBACK: use the original uploaded character photo as
                        # I2V input instead of the last frame. Original photos are never
                        # flagged (they're user-uploaded, not AI-rendered faces).
                        # The CONTINUING FROM text still provides full scene context.
                        if char_photos_raw:
                            ref_bytes, ref_mime, matched = get_clip_character_photo(
                                current_prompt, char_photos_raw
                            )
                            st.warning(
                                f"🔄 Clip {clip['clip']}: last frame flagged as celebrity likeness. "
                                f"Retrying with original **{matched}** photo as I2V anchor "
                                f"(continuity preserved)..."
                            )
                            operation = generate_clip_from_image(
                                video_client, veo_model, current_prompt, ar,
                                clip["clip"], num_clips, ref_bytes, ref_mime,
                            )
                        else:
                            # No reference photos uploaded — last frame had AI-rendered faces
                            # that triggered RAI. Extract just the FIRST frame of the
                            # previous clip (earliest frame = least developed face,
                            # less likely to trigger celebrity detection) and use as I2V.
                            # Gives Veo a scene/lighting anchor even without a clean face ref.
                            prev_path = clip_paths[i - 1]
                            first_frame_path = prev_path.replace(".mp4", "_first_frame.jpg")
                            try:
                                ffmpeg_bin = _get_ffmpeg()
                                subprocess.run(
                                    [ffmpeg_bin, "-y", "-i", prev_path,
                                     "-vframes", "1", "-q:v", "2", first_frame_path],
                                    capture_output=True, text=True,
                                )
                                with open(first_frame_path, "rb") as ff:
                                    first_frame_bytes = ff.read()
                                st.warning(
                                    f"🔄 Clip {clip['clip']}: last frame flagged, no reference photos. "
                                    f"Using first frame of previous clip as I2V anchor..."
                                )
                                operation = generate_clip_from_image(
                                    video_client, veo_model, current_prompt, ar,
                                    clip["clip"], num_clips,
                                    first_frame_bytes, "image/jpeg",
                                )
                            except Exception as ff_err:
                                st.warning(
                                    f"🔄 Clip {clip['clip']}: first-frame extraction failed ({ff_err}). "
                                    f"Last resort: text-only."
                                )
                                operation = generate_clip_text_only(
                                    video_client, veo_model, current_prompt, ar,
                                    clip["clip"], num_clips,
                                )
                        if operation is None:
                            video_obj = None
                        else:
                            try:
                                video_obj = extract_generated_video(operation, clip["clip"])
                            except (RaiCelebrityError, RaiContentError) as e2:
                                st.error(f"❌ Clip {clip['clip']} RAI block on photo fallback too: {e2}")
                                video_obj = None
                    except RaiContentError:
                        # Prompt blocked — let the outer attempt loop handle rephrase
                        video_obj = None

                    if video_obj is not None:
                        break  # success

                    if attempt == MAX_RETRIES:
                        st.error(f"❌ Clip {clip['clip']} failed after {MAX_RETRIES} attempts.")
                        status.update(label=f"❌ Clip {clip['clip']} failed", state="error")
                        st.stop()

                # ── Save clip ─────────────────────────────────────────────────
                clip_path   = os.path.join(TMP, f"superliving_clip_{i+1:02d}.mp4")
                video_bytes = download_video(video_obj.uri, API_KEY)
                with open(clip_path, "wb") as f:
                    f.write(video_bytes)
                clip_paths.append(clip_path)
                st.write(f"✅ Clip {clip['clip']} saved ({len(video_bytes)//1024} KB)")

            status.update(label=f"✅ All {num_clips} clip(s) generated!", state="complete")

        except Exception as e:
            status.update(label="❌ Veo error", state="error")
            st.error(f"Veo error: {e}")
            st.stop()

    # ── STEP 5: Stitch ────────────────────────────────────────────────────────
    final_path = os.path.join(TMP, "superliving_final_ad.mp4")
    if num_clips > 1:
        with st.status("✂️  Stitching clips...", expanded=True) as status:
            ok = stitch_clips(clip_paths, final_path)
            status.update(
                label="✅ Stitched!" if ok else "⚠️ Stitch failed — showing first clip",
                state="complete" if ok else "error",
            )
            if not ok:
                final_path = clip_paths[0]
    else:
        final_path = clip_paths[0]

    # ── Save to session_state → triggers RESULTS DISPLAY PHASE on rerun ──────
    st.session_state["_sl_final_path"]  = final_path
    st.session_state["_sl_clip_paths"]  = clip_paths
    st.session_state["_sl_num_clips"]   = num_clips
    st.session_state["_sl_veo_model"]   = veo_model
    st.session_state["_sl_final_bytes"] = open(final_path, "rb").read()
    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# CLIP REGENERATION PHASE (triggered from results display)
# Regenerates only selected clips, keeps the rest, then re-stitches.
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state.get("_sl_regen_trigger") and not st.session_state.get("_sl_regen_running"):
    st.session_state["_sl_regen_running"] = True
    st.session_state["_sl_regen_trigger"] = False

    regen_indices    = st.session_state["_sl_regen_indices"]      # 0-based list
    clip_data        = st.session_state["_sl_clip_data"]
    clip_paths       = list(st.session_state["_sl_clip_paths"])   # mutable copy
    char_photos_raw  = st.session_state.get("_sl_char_photos_raw", [])
    ar               = st.session_state["_sl_ar"]
    num_clips        = st.session_state["_sl_num_clips"]
    veo_model        = st.session_state["_sl_veo_model"]

    gemini_client = genai.Client(api_key=API_KEY)
    video_client  = genai.Client(api_key=API_KEY, http_options={"api_version": "v1alpha"})

    regen_labels = ", ".join(str(idx + 1) for idx in regen_indices)
    st.markdown("---")
    st.markdown(f"### 🔄 Regenerating Clip(s): {regen_labels}")

    MAX_RETRIES = 3
    regen_ok = True

    with st.status(f"� Regenerating {len(regen_indices)} clip(s)...", expanded=True) as status:
        try:
            for idx in sorted(regen_indices):
                i    = idx            # 0-based clip index
                clip = clip_data[i]
                st.write(f"🎥 Regenerating clip {clip['clip']}/{num_clips}: *{clip['scene_summary']}*")
                current_prompt = clip["prompt"]
                operation      = None

                # ── Pre-sanitize ──────────────────────────────────────────
                with st.spinner(f"🛡️ Pre-sanitizing clip {clip['clip']}..."):
                    try:
                        sanitized = sanitize_prompt_for_veo(
                            gemini_client, current_prompt, clip["clip"]
                        )
                        if sanitized and len(sanitized) > 100:
                            current_prompt = sanitized
                    except Exception:
                        pass

                for attempt in range(1, MAX_RETRIES + 1):
                    if attempt > 1:
                        st.warning(
                            f"⚠️ Clip {clip['clip']} blocked/failed (attempt {attempt-1}). Rephrasing..."
                        )
                        with st.spinner(f"🔄 Rephrasing clip {clip['clip']}..."):
                            current_prompt = rephrase_blocked_prompt(
                                gemini_client, current_prompt, attempt
                            )

                    try:
                        if i == 0:
                            # Clip 1: I2V from original photo or text-only
                            if char_photos_raw:
                                ref_bytes, ref_mime, matched = get_clip_character_photo(
                                    current_prompt, char_photos_raw
                                )
                                st.write(f"📎 Clip 1: using **{matched}** photo as I2V starting frame")
                                operation = generate_clip_from_image(
                                    video_client, veo_model, current_prompt, ar,
                                    clip["clip"], num_clips, ref_bytes, ref_mime,
                                )
                            else:
                                operation = generate_clip_text_only(
                                    video_client, veo_model, current_prompt, ar,
                                    clip["clip"], num_clips,
                                )
                        else:
                            # Clips 2+: use last-frame I2V from the PREVIOUS clip
                            # (which may itself be an already-regenerated clip)
                            prev_path = clip_paths[i - 1]
                            if not os.path.exists(prev_path):
                                raise FileNotFoundError(
                                    f"Previous clip {i} not found at {prev_path}"
                                )
                            next_summary = clip_data[i]["scene_summary"]
                            st.write(
                                f"🎞️ Clip {clip['clip']} (last-frame I2V): "
                                f"continuing from clip {clip['clip']-1}"
                            )
                            operation, current_prompt = generate_clip_with_frame_context(
                                video_client, gemini_client,
                                veo_model, current_prompt, ar,
                                clip["clip"], num_clips,
                                prev_path, next_summary,
                            )

                    except Exception as gen_err:
                        st.warning(f"⚠️ I2V failed: {gen_err} — falling back...")
                        if char_photos_raw:
                            ref_bytes, ref_mime, matched = get_clip_character_photo(
                                current_prompt, char_photos_raw
                            )
                            operation = generate_clip_from_image(
                                video_client, veo_model, current_prompt, ar,
                                clip["clip"], num_clips, ref_bytes, ref_mime,
                            )
                        else:
                            operation = generate_clip_text_only(
                                video_client, veo_model, current_prompt, ar,
                                clip["clip"], num_clips,
                            )

                    if operation is None:
                        if attempt < MAX_RETRIES:
                            continue
                        st.error(f"❌ Clip {clip['clip']} timed out after {MAX_RETRIES} attempts.")
                        regen_ok = False
                        break

                    try:
                        video_obj = extract_generated_video(operation, clip["clip"])
                    except RaiCelebrityError:
                        if char_photos_raw:
                            ref_bytes, ref_mime, matched = get_clip_character_photo(
                                current_prompt, char_photos_raw
                            )
                            st.warning(
                                f"🔄 Clip {clip['clip']}: celebrity flag — "
                                f"retrying with **{matched}** photo..."
                            )
                            operation = generate_clip_from_image(
                                video_client, veo_model, current_prompt, ar,
                                clip["clip"], num_clips, ref_bytes, ref_mime,
                            )
                        else:
                            operation = generate_clip_text_only(
                                video_client, veo_model, current_prompt, ar,
                                clip["clip"], num_clips,
                            )
                        if operation is None:
                            video_obj = None
                        else:
                            try:
                                video_obj = extract_generated_video(operation, clip["clip"])
                            except (RaiCelebrityError, RaiContentError):
                                video_obj = None
                    except RaiContentError:
                        video_obj = None

                    if video_obj is not None:
                        break
                    if attempt == MAX_RETRIES:
                        st.error(f"❌ Clip {clip['clip']} failed after {MAX_RETRIES} attempts.")
                        regen_ok = False

                if not regen_ok:
                    break

                # ── Save regenerated clip (overwrite the old file) ────────
                clip_path   = os.path.join(TMP, f"superliving_clip_{i+1:02d}.mp4")
                video_bytes = download_video(video_obj.uri, API_KEY)
                with open(clip_path, "wb") as f:
                    f.write(video_bytes)
                clip_paths[i] = clip_path
                st.write(f"✅ Clip {clip['clip']} regenerated ({len(video_bytes)//1024} KB)")

            if regen_ok:
                status.update(
                    label=f"✅ {len(regen_indices)} clip(s) regenerated!",
                    state="complete",
                )
            else:
                status.update(label="❌ Some clips failed", state="error")

        except Exception as e:
            status.update(label="❌ Regeneration error", state="error")
            st.error(f"Error: {e}")
            st.code(traceback.format_exc())
            regen_ok = False

    # ── Re-stitch all clips (old + newly regenerated) ─────────────────────
    if regen_ok:
        final_path = os.path.join(TMP, "superliving_final_ad.mp4")
        if num_clips > 1:
            with st.status("✂️ Re-stitching all clips...", expanded=True) as status:
                ok = stitch_clips(clip_paths, final_path)
                status.update(
                    label="✅ Re-stitched!" if ok else "⚠️ Stitch failed",
                    state="complete" if ok else "error",
                )
                if not ok:
                    final_path = clip_paths[0]
        else:
            final_path = clip_paths[0]

        # ── Update session_state with new results ─────────────────────────
        st.session_state["_sl_final_path"]  = final_path
        st.session_state["_sl_clip_paths"]  = clip_paths
        st.session_state["_sl_final_bytes"] = open(final_path, "rb").read()
    else:
        # Regeneration failed — restore the previous final video so the user
        # can see the old result and retry individual clips.
        prev_final = os.path.join(TMP, "superliving_final_ad.mp4")
        if os.path.exists(prev_final):
            st.session_state["_sl_final_path"]  = prev_final
            st.session_state["_sl_final_bytes"] = open(prev_final, "rb").read()
        elif clip_paths:
            # Fallback: show first clip
            st.session_state["_sl_final_path"]  = clip_paths[0]
            st.session_state["_sl_final_bytes"] = open(clip_paths[0], "rb").read()
        st.warning("⚠️ Regeneration failed for some clips. Showing previous version.")

    # Clean up regen flags
    st.session_state.pop("_sl_regen_running", None)
    st.session_state.pop("_sl_regen_indices", None)
    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# RESULTS DISPLAY PHASE
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state.get("_sl_final_path"):
    final_path = st.session_state["_sl_final_path"]
    clip_paths = st.session_state.get("_sl_clip_paths", [])
    num_clips  = st.session_state.get("_sl_num_clips", 1)
    clip_data  = st.session_state.get("_sl_clip_data", [])

    st.markdown("---")
    st.markdown("### 🎉 Your SuperLiving Ad is Ready!")

    if not os.path.exists(final_path):
        st.error(
            f"⚠️ Output file not found at `{final_path}`. "
            "The temp file may have been cleared. Please generate again."
        )
    else:
        # ── Final video player + download ─────────────────────────────────
        if "_sl_final_bytes" not in st.session_state:
            st.session_state["_sl_final_bytes"] = open(final_path, "rb").read()
        final_bytes = st.session_state["_sl_final_bytes"]

        vid_col, dl_col = st.columns([3, 1])
        with vid_col:
            st.video(final_bytes)
        with dl_col:
            st.markdown("<br><br>", unsafe_allow_html=True)
            st.download_button(
                label="⬇️  Download Video (MP4)",
                data=final_bytes,
                file_name="superliving_ad.mp4",
                mime="video/mp4",
                use_container_width=True,
                key="dl_final_video",
            )
            st.caption(f"Duration: ~{num_clips * 8}s | Clips: {num_clips}")

        # ── Per-clip preview, edit & selective regeneration ────────────────
        if len(clip_paths) > 1:
            st.markdown("---")
            st.markdown("### 🎞️ Individual Clips — Preview, Edit & Regenerate")
            st.info(
                "💡 **Selective regeneration:** Check the clips you want to redo, "
                "optionally edit their prompts, then click **Regenerate Selected**. "
                "Unchanged clips are kept as-is and everything is re-stitched automatically."
            )

            regen_checks = []
            for i, p in enumerate(clip_paths):
                clip_num   = i + 1
                clip_label = (
                    clip_data[i]["scene_summary"]
                    if i < len(clip_data) else f"Clip {clip_num}"
                )

                with st.expander(
                    f"Clip {clip_num} — {clip_label}",
                    expanded=False,
                ):
                    col_vid, col_ctrl = st.columns([2, 1])

                    with col_vid:
                        if os.path.exists(p):
                            st.video(open(p, "rb").read())
                        else:
                            st.warning(f"Clip file not found: {p}")

                    with col_ctrl:
                        # Checkbox to mark for regeneration
                        should_regen = st.checkbox(
                            "🔄 Regenerate this clip",
                            key=f"regen_check_{i}",
                            value=False,
                        )
                        regen_checks.append(should_regen)

                        # Download individual clip
                        if os.path.exists(p):
                            st.download_button(
                                f"⬇️ Download Clip {clip_num}",
                                data=open(p, "rb").read(),
                                file_name=f"clip_{clip_num}.mp4",
                                mime="video/mp4",
                                key=f"dl_clip_{i}",
                            )

                    # Editable prompt (shown for all clips, but only used if regenerating)
                    if i < len(clip_data):
                        edited = st.text_area(
                            f"Prompt for Clip {clip_num}",
                            value=clip_data[i]["prompt"],
                            height=250,
                            key=f"regen_prompt_{i}",
                            label_visibility="collapsed",
                        )
                        # Persist edits back to clip_data in session_state
                        if edited != clip_data[i]["prompt"]:
                            st.session_state["_sl_clip_data"][i]["prompt"] = edited

            # ── Regenerate Selected button ────────────────────────────────
            selected = [i for i, checked in enumerate(regen_checks) if checked]

            st.markdown("<br>", unsafe_allow_html=True)
            btn_col1, btn_col2, btn_col3 = st.columns([1, 2, 1])
            with btn_col2:
                if selected:
                    sel_labels = ", ".join(str(s + 1) for s in selected)
                    regen_btn = st.button(
                        f"🔄 Regenerate Clip(s) {sel_labels}",
                        use_container_width=True,
                        key="regen_selected_btn",
                        type="primary",
                    )
                else:
                    st.button(
                        "🔄 Regenerate Selected (select clips above)",
                        use_container_width=True,
                        key="regen_selected_btn_disabled",
                        disabled=True,
                    )
                    regen_btn = False

            if regen_btn and selected:
                st.session_state["_sl_regen_indices"] = selected
                st.session_state["_sl_regen_trigger"] = True
                # Clear old result so the regen phase runs cleanly
                st.session_state.pop("_sl_final_path", None)
                st.session_state.pop("_sl_final_bytes", None)
                st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 Make Another Ad", use_container_width=False, key="reset_btn"):
            for k in list(st.session_state.keys()):
                if k.startswith("_sl_"):
                    del st.session_state[k]
            st.rerun()


# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("<br><br>", unsafe_allow_html=True)
st.markdown(
    '<p style="text-align:center;color:#555;font-size:0.8rem;">'
    'SuperLiving Internal Tool · AI-Powered Ad Generator · 8s max per clip'
    '</p>', unsafe_allow_html=True,
)
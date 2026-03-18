"""
Video processing utilities for Veo.
"""

import logging
import os
import re as _re
import shutil
import subprocess
import tempfile
import traceback
import uuid

try:
    import imageio_ffmpeg
except ImportError:
    imageio_ffmpeg = None

logger = logging.getLogger(__name__)

# ── Portable temp dir ─────────────────────────────────────────────────────────
TMP = tempfile.gettempdir()


def _get_ffmpeg() -> str:
    """Return path to ffmpeg binary or raise."""
    import shutil
    ffmpeg_bin = shutil.which("ffmpeg")
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


def trim_clip_to_duration(input_path: str, output_path: str, target_duration: float = 8.5) -> bool:
    """
    Trim a clip to a target duration (removes excess silence/dead space at end).
    Used when a clip is significantly longer than expected.
    
    Args:
        input_path: Path to input video
        output_path: Path to output trimmed video
        target_duration: Target duration in seconds (default 8.5s for 8-second clips)

    Returns:
        True if trim successful, False otherwise
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        try:
            import imageio_ffmpeg
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            return False
    
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-y", "-i", input_path,
             "-t", str(target_duration),
             "-c:v", "copy", "-c:a", "copy",
             output_path],
            capture_output=True, text=True, timeout=60
        )
        
        if result.returncode == 0 and os.path.exists(output_path):
            logger.info(f"  ✂️ Trimmed {os.path.basename(input_path)} to {target_duration:.1f}s")
            return True
        else:
            logger.warning(f"  ⚠️ Trim failed: {result.stderr[-300:]}")
            return False
    except Exception as e:
        logger.warning(f"  ⚠️ Trim exception: {e}")
        return False


class _CrossfadeSkipped(Exception):
    """Internal signal: crossfade was skipped, proceed to concat fallback."""
    pass

def stitch_clips(clip_paths: list, output_path: str, transition_sec: float = 0.3) -> bool:
    """
    Stitch AI-generated clips into one seamless video with cinematic crossfades,
    zero audio pops, zero A/V desync, and zero timestamp drift.

    ─── THE FIX: 3-STAGE PIPELINE ───
    Stage 1 — Normalization: aresample=async=1, apad, -shortest (A/V sync)
    Stage 2 — Crossfade: filter_complex dynamic chain (xfade + acrossfade)
    Stage 3 — Fallback: Concat demuxer (if crossfade fails)
    """

    # ── Locate ffmpeg binary 
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        try:
            import imageio_ffmpeg
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            pass
    if not ffmpeg_bin:
        logger.error("❌ ffmpeg not found — cannot stitch clips.")
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
        logger.warning(f"⚠️ Could not parse duration for {os.path.basename(path)} — assuming 7.7s")
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
        # STAGE 1: Normalize every clip — video + audio sync
        # ══════════════════════════════════════════════════════════════════════
        normalized = []
        durations  = []

        for i, p in enumerate(clip_paths):
            norm_path = os.path.join(TMP, f"norm_{i:02d}.mp4")
            clip_has_audio = has_audio_stream(p)

            if clip_has_audio:
                logger.info(f"  📎 Clip {i+1}: normalizing video + audio (aresample→apad→shortest)...")
                r = subprocess.run(
                    [ffmpeg_bin, "-y", "-i", p,
                     "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=24,format=yuv420p",
                     "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                     "-pix_fmt", "yuv420p",
                     "-af", "aresample=async=1,apad",
                     "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k",
                     "-shortest",
                     norm_path],
                    capture_output=True, text=True,
                )
                if r.returncode != 0:
                    logger.warning(f"  ⚠️ Clip {i+1}: aresample pipeline failed, trying basic normalize...")
                    logger.debug(f"Clip {i+1} aresample error:\n{r.stderr[-800:]}")
                    
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
                        raise RuntimeError(f"Normalize clip {i+1} failed (both pipelines):\n{r.stderr[-500:]}")

            else:
                vid_dur = probe_duration(p)
                logger.info(f"  🔇 Clip {i+1}: no audio — generating {vid_dur:.3f}s silence track...")
                r = subprocess.run(
                    [ffmpeg_bin, "-y",
                     "-i", p,
                     "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={vid_dur:.4f}",
                     "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=24,format=yuv420p",
                     "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                     "-pix_fmt", "yuv420p",
                     "-map", "0:v:0", "-map", "1:a:0",
                     "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k",
                     "-t", f"{vid_dur:.4f}",
                     norm_path],
                    capture_output=True, text=True,
                )
                if r.returncode != 0:
                    raise RuntimeError(f"Normalize+silence clip {i+1} failed:\n{r.stderr[-500:]}")

            dur = probe_duration(norm_path)
            normalized.append(norm_path)
            durations.append(dur)
            sz = os.path.getsize(norm_path) // 1024
            logger.info(f"  ✅ Clip {i+1}: {dur:.2f}s normalized ({sz} KB)")
            
            # ── Validate clip duration: detect anomalies ───────────────────
            # Expected: ~8 seconds for 8-second generated clips
            # Anomaly: >25 seconds (indicates silence padding or gen error)
            # NOTE: CTA is 16s, so we only trim if way beyond normal range
            if dur > 25.0:
                logger.warning(f"  ⚠️ Clip {i+1} is unusually long ({dur:.2f}s) — trimming to 8.5s")
                trimmed_path = os.path.join(TMP, f"norm_{i:02d}_trimmed.mp4")
                if trim_clip_to_duration(norm_path, trimmed_path, target_duration=8.5):
                    # Replace with trimmed version
                    os.remove(norm_path)
                    shutil.move(trimmed_path, norm_path)
                    dur = probe_duration(norm_path)
                    durations[-1] = dur  # Update duration
                    logger.info(f"  ✅ Clip {i+1} trimmed to {dur:.2f}s")

        if len(normalized) == 1:
            shutil.copy(normalized[0], output_path)
            logger.info("  ✅ Single clip — no stitching needed")
            return True

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 2: Crossfade via filter_complex
        # ══════════════════════════════════════════════════════════════════════
        n = len(normalized)
        T = transition_sec

        clips_too_short = any(d < 2 * T + 0.1 for d in durations)
        if T <= 0 or clips_too_short:
            if clips_too_short and T > 0:
                logger.warning(f"⚠️ Some clips are shorter than {2*T+0.1:.1f}s — skipping crossfades.")
            raise _CrossfadeSkipped("clips too short or transition disabled")

        logger.info(f"  🎬 Building crossfade filter: {n} clips, {n-1} × {T}s fade...")

        v_filters = []
        a_filters = []
        cumulative = durations[0]

        for i in range(n - 1):
            offset = max(0.0, cumulative - T)

            in_v1 = "[0:v]" if i == 0 else f"[vf{i-1}]"
            in_v2 = f"[{i+1}:v]"
            out_v = "[vout]" if i == n - 2 else f"[vf{i}]"
            v_filters.append(f"{in_v1}{in_v2}xfade=transition=fade:duration={T}:offset={offset:.4f}{out_v}")

            in_a1 = "[0:a]" if i == 0 else f"[af{i-1}]"
            in_a2 = f"[{i+1}:a]"
            out_a = "[aout]" if i == n - 2 else f"[af{i}]"
            a_filters.append(f"{in_a1}{in_a2}acrossfade=d={T}:c1=tri:c2=tri{out_a}")

            cumulative = cumulative - T + durations[i + 1]

        filter_complex = ";".join(v_filters + a_filters)

        cmd = [ffmpeg_bin, "-y"]
        for p in normalized:
            cmd += ["-i", p]

        cmd += [
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]

        logger.debug(f"Crossfade filter_complex:\n{filter_complex}")
        logger.info("  ⏳ Running crossfade render (this may take a moment)...")
        r_xfade = subprocess.run(cmd, capture_output=True, text=True)

        if (r_xfade.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 100_000):
            sz = os.path.getsize(output_path) // (1024 * 1024)
            final_dur = probe_duration(output_path)
            expected_dur = sum(durations) - (n - 1) * T
            logger.info(f"  ✅ Final video: {sz} MB, {final_dur:.2f}s (expected ~{expected_dur:.2f}s) — cinematic {T}s crossfades 🎬")
            return True

        logger.warning("⚠️ Crossfade render failed — falling back to hard-cut concat.")
        logger.debug(f"Crossfade error stderr:\n{r_xfade.stderr[-2000:]}")
        raise _CrossfadeSkipped("xfade render failed")

    except _CrossfadeSkipped:
        pass
    except Exception as e:
        logger.error(f"❌ Stitch error during normalization/crossfade: {e}")
        logger.error(traceback.format_exc())
        return False

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 3 — FALLBACK: Concat demuxer (hard cuts, no crossfade)
    # ══════════════════════════════════════════════════════════════════════════
    try:
        logger.info("  🔗 Falling back to concat demuxer (hard cuts)...")

        list_file = os.path.join(TMP, "veo_concat_list.txt")
        with open(list_file, "w") as f:
            for p in normalized:
                safe_path = os.path.abspath(p).replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

        r_copy = subprocess.run(
            [ffmpeg_bin, "-y", "-f", "concat", "-safe", "0",
             "-i", list_file,
             "-c", "copy",
             "-movflags", "+faststart",
             output_path],
            capture_output=True, text=True,
        )

        if (r_copy.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 100_000):
            sz = os.path.getsize(output_path) // (1024 * 1024)
            final_dur = probe_duration(output_path)
            logger.info(f"  ✅ Final video: {sz} MB, {final_dur:.2f}s (stream-copy concat)")
            return True

        logger.warning("⚠️ Stream-copy concat failed — falling back to re-encode concat.")
        logger.debug(f"Stream-copy concat error:\n{r_copy.stderr[-2000:]}")

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

        if (r_reencode.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 100_000):
            sz = os.path.getsize(output_path) // (1024 * 1024)
            final_dur = probe_duration(output_path)
            logger.info(f"  ✅ Final video: {sz} MB, {final_dur:.2f}s (re-encode concat fallback)")
            return True

        logger.error(f"❌ All stitching methods failed.\nCopy error: {r_copy.stderr[-400:]}\nRe-encode error: {r_reencode.stderr[-400:]}")
        return False

    except Exception as e:
        logger.error(f"❌ Unhandled concat fallback error:\n{e}")
        return False
        
def concat_with_normalized_cta(base_vid_path: str, cta_path: str, output_path: str,
                               pause_sec: float = 0.5) -> bool:
    """
    Append *cta_path* after *base_vid_path* with an optional black-frame pause,
    then write the result to *output_path*.

    ROOT CAUSE OF THE STUCK-CTA BUG
    ────────────────────────────────
    The original code normalized the two files separately and then tried to
    stream-copy them together with `concat demuxer -c copy`.  Stream-copy only
    works when EVERY stream parameter is identical: codec, fps, timebase,
    sample-rate, channel layout, and pixel format.  The CTA file is 60 fps /
    48000 Hz; the AI stitched video is 24 fps / 44100 Hz.  After separate
    normalization runs the container timebases can still differ by a fraction,
    which makes the concat demuxer write a file where the CTA segment has
    corrupted PTS — it appears to play but immediately stalls or shows a black
    frame because the player cannot seek into the second segment.

    THE FIX: filter_complex concat (always re-encode, single pass)
    ──────────────────────────────────────────────────────────────
    We feed BOTH inputs into a single ffmpeg invocation and join them via the
    `concat` filter.  The filter resets PTS at every segment boundary and emits
    one continuous timeline.  A full re-encode guarantees every frame has a
    monotonically-increasing, player-friendly timestamp — no stalls, no seeking
    bugs, no codec mismatch.

    PAUSE
    ─────
    A `pause_sec` (default 0.5 s) black+silence segment is inserted between the
    AI video and the CTA using `color` + `anullsrc` sources so the transition
    feels intentional rather than jarring.
    """
    ffmpeg_bin = _get_ffmpeg()

    # ── Shared encode parameters (must be identical for both segments) ────────
    TARGET_W, TARGET_H = 1080, 1920
    TARGET_FPS  = 24
    TARGET_AR   = 44100   # audio sample rate
    TARGET_ACH  = 2       # stereo

    # ── Unique filenames so concurrent requests don't collide ─────────────────
    uid = uuid.uuid4().hex[:8]
    norm_base = os.path.join(TMP, f"norm_base_{uid}.mp4")
    norm_cta  = os.path.join(TMP, f"norm_cta_{uid}.mp4")
    pause_seg = os.path.join(TMP, f"pause_{uid}.mp4")

    def _re_encode(src: str, dst: str, label: str) -> bool:
        """
        Re-encode *src* to a fully normalised MP4:
          • 1080×1920 yuv420p @ 24 fps  (scale-with-pad preserves AR)
          • AAC stereo @ 44100 Hz
          • -movflags +faststart  so the moov atom is at the front —
            this is what lets players seek into the segment without
            downloading the entire file first.
        """
        cmd = [
            ffmpeg_bin, "-y", "-i", src,
            "-vf", (
                f"scale={TARGET_W}:{TARGET_H}"
                f":force_original_aspect_ratio=decrease,"
                f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2,"
                f"format=yuv420p,fps={TARGET_FPS}"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", str(TARGET_AR), "-ac", str(TARGET_ACH), "-b:a", "192k",
            # Force a clean timebase so concat filter has nothing to complain about
            "-video_track_timescale", "12800",
            "-movflags", "+faststart",
            dst,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            logger.error(f"❌ Re-encode failed ({label}):\n{r.stderr[-600:]}")
            return False
        sz = os.path.getsize(dst) // 1024
        logger.info(f"  ✅ Normalised {label}: {sz} KB → {dst}")
        return True

    def _make_pause(dst: str) -> bool:
        """Generate a black-frame + silence segment of *pause_sec* seconds."""
        if pause_sec <= 0:
            return True   # caller skips concatenating this segment
        cmd = [
            ffmpeg_bin, "-y",
            "-f", "lavfi",
            "-i", f"color=black:size={TARGET_W}x{TARGET_H}:rate={TARGET_FPS}:duration={pause_sec}",
            "-f", "lavfi",
            "-i", f"anullsrc=r={TARGET_AR}:cl=stereo:d={pause_sec}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", str(TARGET_AR), "-ac", str(TARGET_ACH), "-b:a", "192k",
            "-t", str(pause_sec),
            "-video_track_timescale", "12800",
            "-movflags", "+faststart",
            dst,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            logger.error(f"❌ Pause segment creation failed:\n{r.stderr[-400:]}")
            return False
        logger.info(f"  ✅ Pause segment ({pause_sec}s) created")
        return True

    # ── Step 1: normalise both inputs ─────────────────────────────────────────
    logger.info("  🔧 Normalising AI video for CTA concat…")
    if not _re_encode(base_vid_path, norm_base, "AI video"):
        return False

    logger.info("  🔧 Normalising CTA video…")
    if not _re_encode(cta_path, norm_cta, "CTA"):
        return False

    # ── Step 2: create pause segment (may be skipped if pause_sec=0) ─────────
    use_pause = pause_sec > 0
    if use_pause:
        logger.info(f"  🔧 Creating {pause_sec}s black pause segment…")
        if not _make_pause(pause_seg):
            use_pause = False   # non-fatal — skip pause, still concat

    # ── Step 3: join via filter_complex concat (single re-encode pass) ────────
    #
    # WHY filter_complex and not concat demuxer?
    # The concat demuxer with -c copy is the fast path but requires IDENTICAL
    # codec / timebase metadata.  Even after separate re-encodes there can be
    # sub-frame timebase differences that corrupt PTS in the second segment.
    # The concat filter recalculates PTS from scratch for every segment — it is
    # the only way to guarantee a single, monotonic timeline.
    #
    segments = [norm_base]
    if use_pause:
        segments.append(pause_seg)
    segments.append(norm_cta)

    n = len(segments)
    cmd = [ffmpeg_bin, "-y"]
    for seg in segments:
        cmd += ["-i", seg]

    # Build "[0:v][0:a][1:v][1:a]...[n-1:v][n-1:a]concat=n=N:v=1:a=1[vout][aout]"
    stream_pairs = "".join(f"[{i}:v][{i}:a]" for i in range(n))
    filter_str   = f"{stream_pairs}concat=n={n}:v=1:a=1[vout][aout]"

    cmd += [
        "-filter_complex", filter_str,
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", str(TARGET_AR), "-ac", str(TARGET_ACH), "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]

    logger.info(f"  🎬 Joining {n} segment(s) via filter_complex concat…")
    r = subprocess.run(cmd, capture_output=True, text=True)

    if r.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) < 50_000:
        logger.error(f"❌ filter_complex concat failed:\n{r.stderr[-800:]}")
        # ── Fallback: concat demuxer with full re-encode (no stream copy) ──
        logger.info("  ↩️ Falling back to concat demuxer + re-encode…")
        list_file = os.path.join(TMP, f"concat_cta_{uid}.txt")
        with open(list_file, "w") as fh:
            for seg in segments:
                safe = os.path.abspath(seg).replace("\\", "/")
                fh.write(f"file '{safe}'\n")

        r2 = subprocess.run(
            [ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", list_file,
             "-vf", f"fps={TARGET_FPS},format=yuv420p",
             "-c:v", "libx264", "-preset", "fast", "-crf", "18",
             "-pix_fmt", "yuv420p",
             "-af", f"aresample={TARGET_AR}",
             "-c:a", "aac", "-ar", str(TARGET_AR), "-ac", str(TARGET_ACH), "-b:a", "192k",
             "-movflags", "+faststart",
             output_path],
            capture_output=True, text=True,
        )
        if r2.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) < 50_000:
            logger.error(f"❌ Fallback concat also failed:\n{r2.stderr[-400:]}")
            return False
        logger.info("  ✅ Fallback concat succeeded")

    sz = os.path.getsize(output_path) // (1024 * 1024)
    logger.info(f"  ✅ Final video with CTA ready: {output_path} ({sz} MB)")
    return True
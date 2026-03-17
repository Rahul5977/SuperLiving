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
CTA_NORMALIZE_FILTER = "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=30"  # pixel format handled via -pix_fmt yuv420p


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


def concat_with_normalized_cta(clip_paths: list[str], output_path: str, cta_is_normalized: bool = False) -> bool:
    """
    Fast concat pipeline for AI clips + CTA.

    CTA is pre-normalized to a stable spec (H.264, yuv420p, 30fps,
    AAC 44.1k stereo, video_track_timescale 90000) even if upstream clips
    use a different cadence. If cta_is_normalized=True, the CTA is assumed
    to already meet these specs and will be stream-copied. Normalization
    happens before running the concat demuxer with stream copy. This
    prevents header/decoder issues on local players when the CTA's
    technical profile differs from the generated clips.
    """
    if len(clip_paths) < 2:
        raise ValueError("clip_paths must include at least one AI clip and the CTA as the final entry.")

    ffmpeg_bin = _get_ffmpeg()

    # Ensure all sources exist before doing any work
    for p in clip_paths:
        if not os.path.exists(p):
            raise FileNotFoundError(f"Input clip not found: {p}")

    input_clips, cta_clip = clip_paths[:-1], clip_paths[-1]
    unique_suffix = uuid.uuid4().hex  # UUID avoids collisions across concurrent requests
    normalized_cta = os.path.join(TMP, f"cta_normalized_concat_{unique_suffix}.mp4")
    concat_list = os.path.join(TMP, f"cta_concat_list_{unique_suffix}.txt")
    created_cta = False

    try:
        # Normalize CTA to the required spec unless already normalized
        if cta_is_normalized:
            normalized_cta = cta_clip
        else:
            norm_cmd = [
                ffmpeg_bin,
                "-y",
                "-i", cta_clip,
                # Single vf chain keeps scaling (even dims) + target fps together
                "-vf", CTA_NORMALIZE_FILTER,
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-ar", "44100",
                "-ac", "2",
                "-video_track_timescale", "90000",
                normalized_cta,
            ]
            subprocess.run(norm_cmd, check=True, capture_output=True, text=True)
            created_cta = True

        # Build concat list with normalized CTA
        with open(concat_list, "w") as f:
            for src in input_clips + [normalized_cta]:
                safe_path = os.path.abspath(src).replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

        concat_cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list,
            "-c", "copy",
            output_path,
        ]
        subprocess.run(concat_cmd, check=True, capture_output=True, text=True)
        return True

    except subprocess.CalledProcessError as exc:
        logger.error("FFmpeg concat pipeline failed: %s", exc.stderr or exc.stdout or exc)
        return False
    except Exception:
        logger.error("Unexpected error during CTA concat:\n%s", traceback.format_exc())
        return False
    finally:
        for tmp_path in filter(None, (concat_list, normalized_cta if created_cta else None)):
            try:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception as cleanup_err:
                logger.debug("Cleanup of temp file failed: %s", cleanup_err)


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
        logger.error(f"❌ Stitch error (concat fallback): {e}")
        logger.error(traceback.format_exc())
        return False

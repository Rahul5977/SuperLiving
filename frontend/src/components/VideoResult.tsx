"use client";

import { Dispatch, SetStateAction, useState } from "react";
import { motion } from "framer-motion";
import type { ClipPrompt } from "@/app/page";

/* ─── Props ─────────────────────────────────────────────────────────────── */

interface Props {
  videoUrl: string;
  clips: ClipPrompt[];
  setClips: Dispatch<SetStateAction<ClipPrompt[]>>;
  numClips: number;
  onRegenerate: (indices: number[]) => Promise<void>;
  onReset: () => void;
  loading: boolean;
}

/* ─── Component ─────────────────────────────────────────────────────────── */

export default function VideoResult({
  videoUrl,
  clips,
  setClips,
  numClips,
  onRegenerate,
  onReset,
  loading,
}: Props) {
  const [regenChecks, setRegenChecks] = useState<boolean[]>(
    new Array(clips.length).fill(false)
  );

  const toggleCheck = (i: number) => {
    setRegenChecks((prev) => {
      const copy = [...prev];
      copy[i] = !copy[i];
      return copy;
    });
  };

  const selectedIndices = regenChecks
    .map((checked, i) => (checked ? i : -1))
    .filter((i) => i >= 0);

  const updateClipPrompt = (index: number, value: string) => {
    setClips((prev) => {
      const copy = [...prev];
      copy[index] = { ...copy[index], prompt: value };
      return copy;
    });
  };

  return (
    <div className="space-y-6 min-w-0 w-full overflow-hidden">
      {/* ── Final Video — constrained, no overflow ─────────────────────── */}
      <div>
        <h2 className="mb-4 text-xl font-bold text-white">
          🎉 Your SuperLiving Ad is Ready!
        </h2>

        <div
          className="overflow-hidden rounded-2xl border"
          style={{
            background: "rgba(255,255,255,0.04)",
            borderColor: "rgba(37,168,90,0.18)",
          }}
        >
          {/* 
            KEY FIX: Use a single-column layout on mobile,
            two-column (video + actions) only on lg screens.
            Video is strictly max-w-full, max-h-[420px] so it never blows out.
          */}
          <div className="p-5">
            <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
              {/* Video — always constrained */}
              <div className="min-w-0 flex-1">
                <video
                  src={videoUrl}
                  controls
                  playsInline
                  className="w-full rounded-xl object-contain"
                  style={{ maxHeight: "420px" }}
                />
              </div>

              {/* Actions sidebar */}
              <div className="flex shrink-0 flex-row flex-wrap items-center gap-3 lg:w-44 lg:flex-col lg:items-stretch">
                <a
                  href={videoUrl}
                  download="superliving_ad.mp4"
                  className="flex items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold text-white transition hover:opacity-90"
                  style={{
                    background: "linear-gradient(90deg, #1a7a3c, #25a85a)",
                  }}
                >
                  ⬇️ Download MP4
                </a>
                <div className="flex flex-col items-center gap-0.5 text-center">
                  <p className="text-xs text-white/40">
                    ~{numClips * 8}s duration
                  </p>
                  <p className="text-xs text-white/30">{numClips} clips</p>
                </div>
                <button
                  onClick={onReset}
                  className="rounded-lg border border-white/15 px-3 py-2 text-xs text-white/50 transition hover:bg-white/5 hover:text-white"
                >
                  🔄 New Ad
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ── Individual Clips ─────────────────────────────────────────── */}
      {clips.length > 1 && (
        <div>
          <h3 className="mb-2 text-lg font-bold text-white">
            🎞️ Individual Clips — Preview, Edit &amp; Regenerate
          </h3>
          <p className="mb-4 text-xs text-white/50">
            💡 Check the clips you want to redo, optionally edit their prompts,
            then click <strong>Regenerate Selected</strong>. Unchanged clips are
            kept as-is.
          </p>

          <div className="space-y-4">
            {clips.map((clip, i) => (
              <motion.div
                key={clip.clip}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.04 }}
                className="min-w-0 rounded-2xl border p-5"
                style={{
                  background: "rgba(255,255,255,0.04)",
                  borderColor: regenChecks[i]
                    ? "rgba(37,168,90,0.5)"
                    : "rgba(37,168,90,0.18)",
                }}
              >
                <div className="mb-3 flex min-w-0 items-center gap-3">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#25a85a]/20 text-xs font-bold text-[#25a85a]">
                    {clip.clip}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm text-white/70">
                    {clip.scene_summary}
                  </span>
                  <label className="flex shrink-0 cursor-pointer items-center gap-2">
                    <input
                      type="checkbox"
                      checked={regenChecks[i] || false}
                      onChange={() => toggleCheck(i)}
                      className="h-4 w-4 rounded border-white/20 accent-[#25a85a]"
                    />
                    <span className="text-xs text-white/50">🔄 Regen</span>
                  </label>
                </div>

                <textarea
                  value={clip.prompt}
                  onChange={(e) => updateClipPrompt(i, e.target.value)}
                  rows={8}
                  className="w-full min-w-0 resize-y rounded-xl border border-white/10 bg-white/5 px-4 py-3 font-mono text-xs leading-relaxed text-white/70 outline-none focus:border-[#25a85a]/60 focus:ring-1 focus:ring-[#25a85a]/40"
                />
              </motion.div>
            ))}
          </div>

          {/* ── Regenerate Button ─────────────────────────────────────── */}
          <div className="mt-6 flex flex-wrap items-center justify-center gap-4">
            {selectedIndices.length > 0 ? (
              <button
                onClick={() => onRegenerate(selectedIndices)}
                disabled={loading}
                className="rounded-xl px-8 py-3 text-sm font-bold text-white transition-all hover:opacity-90 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50"
                style={{
                  background: "linear-gradient(90deg, #1a7a3c, #25a85a)",
                }}
              >
                {loading ? (
                  <span className="flex items-center gap-2">
                    <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24">
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                        fill="none"
                      />
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                      />
                    </svg>
                    Regenerating…
                  </span>
                ) : (
                  `🔄  Regenerate Clip(s) ${selectedIndices
                    .map((i) => i + 1)
                    .join(", ")}`
                )}
              </button>
            ) : (
              <button
                disabled
                className="cursor-not-allowed rounded-xl bg-white/10 px-8 py-3 text-sm text-white/30"
              >
                🔄 Regenerate Selected (select clips above)
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
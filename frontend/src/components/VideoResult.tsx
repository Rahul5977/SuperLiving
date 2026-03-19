"use client";

import { Dispatch, SetStateAction, useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { ClipPrompt } from "@/lib/api";
import type { Job } from "@/hooks/useJobPoller";

interface Props {
  videoUrl: string;
  clips: ClipPrompt[];
  setClips: Dispatch<SetStateAction<ClipPrompt[]>>;
  numClips: number;
  onRegenerate: (indices: number[]) => Promise<void>;
  onReset: () => void;
  loading: boolean;
  activeJob?: Job | null;
}

const REGEN_STEPS = [
  "Sanitizing prompts…",
  "Re-generating clip…",
  "Analyzing frame continuity…",
  "Applying visual anchors…",
  "Re-stitching video…",
  "Appending CTA…",
  "Finalizing…",
];

export default function VideoResult({
  videoUrl, clips, setClips, numClips,
  onRegenerate, onReset, loading, activeJob,
}: Props) {
  const [regenChecks, setRegenChecks] = useState<boolean[]>(
    () => new Array(clips.length).fill(false)
  );
  const [fallbackIdx, setFallbackIdx] = useState(0);

  useEffect(() => {
    if (!loading) return;
    const t = setInterval(() => setFallbackIdx((i) => (i + 1) % REGEN_STEPS.length), 2000);
    return () => {
      clearInterval(t);
      setFallbackIdx(0);
    };
  }, [loading]);

  const toggleCheck = (i: number) =>
    setRegenChecks((prev) => { const c = [...prev]; c[i] = !c[i]; return c; });

  const selectedIndices = regenChecks
    .map((c, i) => (c ? i : -1))
    .filter((i) => i >= 0);

  const updateClipPrompt = (index: number, value: string) =>
    setClips((prev) => {
      const c = [...prev];
      c[index] = { ...c[index], prompt: value };
      return c;
    });

  const displayStep = activeJob?.step ?? REGEN_STEPS[fallbackIdx];
  const displayProgress = activeJob?.progress ?? 0;

  return (
    <div className="space-y-8">

      {/* ── Final video ──────────────────────────────────────────── */}
      <motion.section initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
        <div className="mb-4 flex items-center gap-3">
          <div
            className="flex h-8 w-8 items-center justify-center rounded-xl text-base"
            style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))", boxShadow: "0 0 16px var(--accent-glow)" }}
          >
            🎉
          </div>
          <h2 className="text-xl font-bold" style={{ color: "var(--text-primary)" }}>
            Your SuperLiving Ad is Ready
          </h2>
        </div>

        <div
          className="glass overflow-hidden rounded-2xl"
          style={{ boxShadow: "0 0 48px rgba(99,102,241,0.10), var(--shadow-card)" }}
        >
          <div className="grid gap-6 p-6 lg:grid-cols-[1fr_180px]">
            {/* Video / skeleton */}
            <div>
              {loading ? (
                <div
                  className="skeleton w-full rounded-xl"
                  style={{ aspectRatio: "16/9" }}
                />
              ) : (
                <video
                  src={videoUrl}
                  controls
                  className="w-full rounded-xl"
                  style={{ maxHeight: 480, boxShadow: "0 4px 32px rgba(0,0,0,0.5)" }}
                />
              )}
            </div>

            {/* Side panel */}
            <div className="flex flex-col justify-center gap-3">
              {loading ? (
                /* Regen progress */
                <div
                  className="glass rounded-xl p-4"
                  style={{ borderColor: "var(--border-bright)" }}
                >
                  <div className="mb-2 flex items-center gap-2">
                    <span
                      className="h-2 w-2 rounded-full"
                      style={{
                        background: "var(--accent)",
                        boxShadow: "0 0 8px var(--accent-glow)",
                        animation: "pulse-dot 1.4s ease-in-out infinite",
                      }}
                    />
                    <span className="text-xs font-medium" style={{ color: "var(--accent-bright)" }}>
                      Regenerating…
                    </span>
                  </div>

                  <div
                    className="h-1 w-full overflow-hidden rounded-full mb-2"
                    style={{ background: "rgba(255,255,255,0.06)" }}
                  >
                    <div
                      className="progress-bar-fill h-full rounded-full"
                      style={{ width: `${displayProgress}%` }}
                    />
                  </div>

                  <AnimatePresence mode="wait">
                    <motion.p
                      key={displayStep}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.15 }}
                      className="text-xs"
                      style={{ color: "var(--text-secondary)" }}
                    >
                      {displayStep}
                    </motion.p>
                  </AnimatePresence>

                  <p className="mt-2 text-xs font-mono text-right" style={{ color: "var(--text-muted)" }}>
                    {displayProgress}%
                  </p>
                </div>
              ) : (
                <>
                  <a
                    href={videoUrl}
                    download="superliving_ad.mp4"
                    className="btn-primary flex items-center justify-center gap-2 rounded-xl py-3 text-sm font-semibold"
                  >
                    ⬇ Download MP4
                  </a>

                  <div
                    className="rounded-xl p-3 text-center"
                    style={{ background: "rgba(255,255,255,0.025)", border: "1px solid var(--border-subtle)" }}
                  >
                    <p className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
                      ~{numClips * 8}s · {numClips} clips
                    </p>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </motion.section>

      {/* ── Individual clips ─────────────────────────────────────── */}
      {clips.length > 1 && (
        <section>
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h3 className="text-base font-bold" style={{ color: "var(--text-primary)" }}>
                Individual Clips
              </h3>
              <p className="mt-0.5 text-xs" style={{ color: "var(--text-muted)" }}>
                Select clips to regenerate · Edit prompts · Click Regenerate
              </p>
            </div>

            <AnimatePresence>
              {selectedIndices.length > 0 && (
                <motion.span
                  initial={{ opacity: 0, scale: 0.85 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.85 }}
                  className="rounded-xl px-3 py-1 text-xs font-semibold"
                  style={{ background: "var(--accent-glow)", color: "var(--accent-bright)" }}
                >
                  {selectedIndices.length} selected
                </motion.span>
              )}
            </AnimatePresence>
          </div>

          <div className="space-y-3">
            {clips.map((clip, i) => (
              <motion.div
                key={clip.clip}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.04, duration: 0.3 }}
                className="glass rounded-2xl p-4 transition-all duration-200"
                style={{
                  borderColor: regenChecks[i] ? "var(--accent)" : undefined,
                  boxShadow: regenChecks[i] ? "0 0 18px var(--accent-glow)" : undefined,
                }}
              >
                {/* Clip header row */}
                <div className="mb-3 flex items-center gap-3">
                  <span
                    className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-xl text-xs font-bold transition-all duration-200"
                    style={{
                      background: regenChecks[i]
                        ? "linear-gradient(135deg, var(--accent), var(--accent-2))"
                        : "rgba(255,255,255,0.06)",
                      color: regenChecks[i] ? "white" : "var(--text-muted)",
                      boxShadow: regenChecks[i] ? "0 0 8px var(--accent-glow)" : "none",
                    }}
                  >
                    {clip.clip}
                  </span>

                  <span className="flex-1 text-sm truncate" style={{ color: "var(--text-secondary)" }}>
                    {clip.scene_summary}
                  </span>

                  <label className="flex cursor-pointer select-none items-center gap-2">
                    <span className="text-xs" style={{ color: "var(--text-muted)" }}>Regen</span>
                    <div
                      className="relative h-5 w-9 rounded-full transition-colors duration-200"
                      style={{ background: regenChecks[i] ? "var(--accent)" : "rgba(255,255,255,0.1)" }}
                      onClick={() => toggleCheck(i)}
                    >
                      <div
                        className="absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform duration-200"
                        style={{ left: regenChecks[i] ? "calc(100% - 18px)" : "2px" }}
                      />
                    </div>
                  </label>
                </div>

                {/* Editable prompt */}
                <textarea
                  value={clip.prompt}
                  onChange={(e) => updateClipPrompt(i, e.target.value)}
                  rows={7}
                  className="field w-full resize-y rounded-xl px-4 py-3 font-mono text-xs leading-relaxed"
                  style={{ color: "var(--text-secondary)" }}
                />
              </motion.div>
            ))}
          </div>

          {/* Regen action bar */}
          <div className="mt-6 flex flex-wrap items-center justify-center gap-4">
            {selectedIndices.length > 0 ? (
              <button
                onClick={() => onRegenerate(selectedIndices)}
                disabled={loading}
                className="btn-primary rounded-2xl px-8 py-3 text-sm font-semibold"
              >
                {loading ? (
                  <span className="flex items-center gap-2">
                    <Spinner />
                    <AnimatePresence mode="wait">
                      <motion.span
                        key={displayStep}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.15 }}
                      >
                        {displayStep}
                      </motion.span>
                    </AnimatePresence>
                  </span>
                ) : (
                  `🔄 Regenerate Clip${selectedIndices.length > 1 ? "s" : ""} ${selectedIndices.map((i) => i + 1).join(", ")}`
                )}
              </button>
            ) : (
              <button
                disabled
                className="cursor-not-allowed rounded-2xl px-8 py-3 text-sm"
                style={{ background: "rgba(255,255,255,0.04)", color: "var(--text-muted)" }}
              >
                🔄 Select clips above to regenerate
              </button>
            )}
          </div>
        </section>
      )}

      {/* ── Make another ─────────────────────────────────────────── */}
      <div className="flex justify-center">
        <button
          onClick={onReset}
          className="glass rounded-xl px-8 py-2.5 text-sm transition-all hover:border-indigo-500/40"
          style={{ color: "var(--text-secondary)" }}
        >
          ✦ Make Another Ad
        </button>
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <svg className="h-4 w-4 flex-shrink-0 animate-spin" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}
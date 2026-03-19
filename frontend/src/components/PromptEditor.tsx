"use client";

import { Dispatch, SetStateAction, useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { ClipPrompt } from "@/lib/api";
import type { Job } from "@/hooks/useJobPoller";

interface Props {
  clips: ClipPrompt[];
  setClips: Dispatch<SetStateAction<ClipPrompt[]>>;
  characterSheet: string;
  setCharacterSheet: Dispatch<SetStateAction<string>>;
  onConfirm: () => void;
  onBack: () => void;
  loading: boolean;
  activeJob?: Job | null;
}

const GEN_STEPS = [
  "Sanitizing prompts…",
  "Generating clip 1…",
  "Analyzing rendered frames…",
  "Generating clip 2 with I2V…",
  "Building visual continuity…",
  "Rendering next scene…",
  "Stitching clips together…",
  "Appending CTA…",
  "Finalizing output…",
];

export default function PromptEditor({
  clips, setClips, characterSheet, setCharacterSheet,
  onConfirm, onBack, loading, activeJob,
}: Props) {
  const [fallbackIdx, setFallbackIdx] = useState(0);

  useEffect(() => {
    if (!loading) return;
    const t = setInterval(() => setFallbackIdx((i) => (i + 1) % GEN_STEPS.length), 2200);
    return () => {
      clearInterval(t);
      setFallbackIdx(0);
    };
  }, [loading]);

  const updateClip = (index: number, field: keyof ClipPrompt, value: string) => {
    setClips((prev) => {
      const copy = [...prev];
      copy[index] = { ...copy[index], [field]: value };
      return copy;
    });
  };

  const downloadPrompts = () => {
    const text = clips
      .map((c) => `CLIP ${c.clip} — ${c.scene_summary}\n${c.prompt}`)
      .join("\n\n---\n\n");
    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "superliving_prompts.txt";
    a.click();
    URL.revokeObjectURL(url);
  };

  const displayStep = activeJob?.step ?? GEN_STEPS[fallbackIdx];
  const displayProgress = activeJob?.progress ?? 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold" style={{ color: "var(--text-primary)" }}>
            Review &amp; Edit Prompts
          </h2>
          <p className="mt-1 text-sm" style={{ color: "var(--text-muted)" }}>
            {clips.length} clips ready · Edit any section · Confirm when satisfied
          </p>
        </div>
        <button
          onClick={onBack}
          className="glass flex-shrink-0 rounded-xl px-4 py-2 text-sm transition-all hover:border-indigo-500/40"
          style={{ color: "var(--text-secondary)" }}
        >
          ← Back
        </button>
      </div>

      {/* Generation progress card */}
      <AnimatePresence>
        {loading && (
          <motion.div
            initial={{ opacity: 0, y: -10, height: 0 }}
            animate={{ opacity: 1, y: 0, height: "auto" }}
            exit={{ opacity: 0, y: -10, height: 0 }}
            className="glass overflow-hidden rounded-2xl p-5"
            style={{ borderColor: "var(--border-bright)", boxShadow: "0 0 24px var(--accent-glow)" }}
          >
            {/* Title row */}
            <div className="mb-3 flex items-center gap-2.5">
              <span
                className="h-2.5 w-2.5 rounded-full flex-shrink-0"
                style={{
                  background: "var(--accent)",
                  boxShadow: "0 0 10px var(--accent-glow)",
                  animation: "pulse-dot 1.4s ease-in-out infinite",
                }}
              />
              <span className="text-sm font-semibold" style={{ color: "var(--accent-bright)" }}>
                Generating your ad video…
              </span>
              <span className="ml-auto text-xs font-mono" style={{ color: "var(--text-muted)" }}>
                {displayProgress}%
              </span>
            </div>

            {/* Progress bar */}
            <div
              className="h-1.5 w-full overflow-hidden rounded-full mb-3"
              style={{ background: "rgba(255,255,255,0.06)" }}
            >
              <div
                className="progress-bar-fill h-full rounded-full"
                style={{ width: `${displayProgress}%` }}
              />
            </div>

            {/* Cycling step text */}
            <AnimatePresence mode="wait">
              <motion.p
                key={displayStep}
                initial={{ opacity: 0, x: 6 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -6 }}
                transition={{ duration: 0.18 }}
                className="text-xs"
                style={{ color: "var(--text-secondary)" }}
              >
                {displayStep}
              </motion.p>
            </AnimatePresence>

            {/* Tip */}
            <p className="mt-3 text-xs" style={{ color: "var(--text-muted)" }}>
              💡 You can start another generation simultaneously — up to 4 jobs run in parallel.
              Track progress in the sidebar →
            </p>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Character Sheet */}
      {characterSheet && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="glass rounded-2xl p-5"
        >
          <p className="mb-2 text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>
            👥 Character Sheet
          </p>
          <textarea
            value={characterSheet}
            onChange={(e) => setCharacterSheet(e.target.value)}
            rows={7}
            className="field w-full resize-y rounded-xl px-4 py-3 text-xs font-mono leading-relaxed"
          />
        </motion.div>
      )}

      {/* Clip prompts */}
      <div>
        <p className="mb-3 text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>
          🎬 Clip Prompts — {clips.length} clips
        </p>
        <div className="space-y-3">
          {clips.map((clip, i) => (
            <motion.div
              key={clip.clip}
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.045, duration: 0.3, ease: [0.22, 1, 0.36, 1] as [number, number, number, number] }}
              className="glass rounded-2xl p-4"
            >
              {/* Clip header */}
              <div className="mb-3 flex items-center gap-3">
                <span
                  className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-xl text-xs font-bold"
                  style={{
                    background: "linear-gradient(135deg, var(--accent), var(--accent-2))",
                    color: "white",
                    boxShadow: "0 0 8px var(--accent-glow)",
                  }}
                >
                  {clip.clip}
                </span>
                <input
                  type="text"
                  value={clip.scene_summary}
                  onChange={(e) => updateClip(i, "scene_summary", e.target.value)}
                  className="field flex-1 rounded-lg px-3 py-1.5 text-sm"
                  placeholder="Scene summary"
                />
              </div>

              {/* Prompt textarea */}
              <textarea
                value={clip.prompt}
                onChange={(e) => updateClip(i, "prompt", e.target.value)}
                rows={10}
                className="field w-full resize-y rounded-xl px-4 py-3 font-mono text-xs leading-relaxed"
                style={{ color: "var(--text-secondary)" }}
              />
            </motion.div>
          ))}
        </div>
      </div>

      {/* Action bar */}
      <div className="flex flex-wrap items-center justify-center gap-4 pt-2">
        <button
          onClick={downloadPrompts}
          className="glass rounded-xl px-6 py-2.5 text-sm transition-all hover:border-indigo-500/40"
          style={{ color: "var(--text-secondary)" }}
        >
          ⬇ Download Prompts
        </button>

        <button
          onClick={onConfirm}
          disabled={loading}
          className="btn-primary rounded-2xl px-10 py-3.5 text-base font-semibold"
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
            "✦ Confirm & Generate Video"
          )}
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
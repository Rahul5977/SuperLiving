"use client";

import { Dispatch, SetStateAction } from "react";
import { motion } from "framer-motion";
import type { ClipPrompt } from "@/app/page";

/* ─── Props ─────────────────────────────────────────────────────────────── */

interface Props {
  clips: ClipPrompt[];
  setClips: Dispatch<SetStateAction<ClipPrompt[]>>;
  characterSheet: string;
  setCharacterSheet: Dispatch<SetStateAction<string>>;
  onVerify: () => void;       // ← opens Gemini verify phase
  onConfirm: () => void;
  onBack: () => void;
  loading: boolean;
}

/* ─── Component ─────────────────────────────────────────────────────────── */

export default function PromptEditor({
  clips,
  setClips,
  characterSheet,
  setCharacterSheet,
  onVerify,
  onConfirm,
  onBack,
  loading,
}: Props) {
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

  return (
    <div className="space-y-6">
      {/* ── Header ────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white">
            ✏️ Review &amp; Edit Before Generating
          </h2>
          <p className="mt-1 text-sm text-white/60">
            Edit dialogue, scenes, lighting. When ready, verify with Gemini or generate directly.
          </p>
        </div>
        <button
          onClick={onBack}
          className="rounded-lg border border-white/15 px-4 py-2 text-sm text-white/60 transition hover:bg-white/5 hover:text-white"
        >
          ← Back
        </button>
      </div>

      {/* ── Gemini Verify Banner ──────────────────────────────────────── */}
      <div
        className="rounded-2xl border p-4 flex items-center justify-between gap-4"
        style={{
          background: "rgba(99,102,241,0.08)",
          borderColor: "rgba(99,102,241,0.3)",
        }}
      >
        <div className="flex items-center gap-3">
          <span className="text-2xl">✨</span>
          <div>
            <p className="text-sm font-semibold text-indigo-300">Gemini Emotional Verification</p>
            <p className="text-xs text-white/50">
              Enriches prompts with emotional depth — micro-expressions, body language, and sensory details before Veo generation.
            </p>
          </div>
        </div>
        <button
          onClick={onVerify}
          disabled={loading}
          className="shrink-0 rounded-xl px-5 py-2.5 text-sm font-bold text-white transition-all hover:opacity-90 hover:-translate-y-0.5 disabled:opacity-50 flex items-center gap-1.5"
          style={{ background: "linear-gradient(90deg, #4338ca, #6366f1)" }}
        >
          ✨ Verify Prompts
        </button>
      </div>

      {/* ── Character Sheet (no-photos path) ─────────────────────────── */}
      {characterSheet && (
        <div
          className="rounded-2xl border p-6"
          style={{
            background: "rgba(255,255,255,0.04)",
            borderColor: "rgba(37,168,90,0.18)",
          }}
        >
          <label className="mb-2 block text-sm font-semibold text-[#7ecfa0]">
            👥 Character Sheet
          </label>
          <textarea
            value={characterSheet}
            onChange={(e) => setCharacterSheet(e.target.value)}
            rows={8}
            className="w-full resize-y rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white placeholder-white/30 outline-none focus:border-[#25a85a]/60 focus:ring-1 focus:ring-[#25a85a]/40"
          />
        </div>
      )}

      {/* ── Clip Prompts ─────────────────────────────────────────────── */}
      <div className="space-y-4">
        <label className="block text-sm font-semibold text-[#7ecfa0]">
          🎬 Clip Prompts
        </label>
        <p className="text-xs text-white/50">
          Edit freely — outfit lines, dialogue, camera, anything.
        </p>

        {clips.map((clip, i) => (
          <motion.div
            key={clip.clip}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.05 }}
            className="rounded-2xl border p-5"
            style={{
              background: "rgba(255,255,255,0.04)",
              borderColor: "rgba(37,168,90,0.18)",
            }}
          >
            <div className="mb-3 flex items-center gap-3">
              <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[#25a85a]/20 text-xs font-bold text-[#25a85a]">
                {clip.clip}
              </span>
              <input
                type="text"
                value={clip.scene_summary}
                onChange={(e) => updateClip(i, "scene_summary", e.target.value)}
                className="flex-1 rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-white outline-none focus:border-[#25a85a]/60"
                placeholder="Scene summary"
              />
            </div>
            <textarea
              value={clip.prompt}
              onChange={(e) => updateClip(i, "prompt", e.target.value)}
              rows={12}
              className="w-full resize-y rounded-xl border border-white/10 bg-white/5 px-4 py-3 font-mono text-xs leading-relaxed text-white/80 outline-none focus:border-[#25a85a]/60 focus:ring-1 focus:ring-[#25a85a]/40"
            />
          </motion.div>
        ))}
      </div>

      {/* ── Actions ──────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-center gap-4">
        <button
          onClick={downloadPrompts}
          className="rounded-lg border border-white/15 px-5 py-2 text-sm text-white/60 transition hover:bg-white/5 hover:text-white"
        >
          ⬇️ Download Prompts
        </button>

        <button
          onClick={onVerify}
          disabled={loading}
          className="rounded-xl px-6 py-3 text-sm font-bold text-white transition-all hover:opacity-90 hover:-translate-y-0.5 disabled:opacity-50 flex items-center gap-1.5"
          style={{ background: "linear-gradient(90deg, #4338ca, #6366f1)" }}
        >
          ✨ Verify with Gemini
        </button>

        <button
          onClick={onConfirm}
          disabled={loading}
          className="rounded-xl px-10 py-3 text-lg font-bold text-white transition-all hover:opacity-90 hover:-translate-y-0.5 disabled:opacity-50 disabled:cursor-not-allowed"
          style={{ background: "linear-gradient(90deg, #1a7a3c, #25a85a)" }}
        >
          {loading ? (
            <span className="flex items-center gap-2">
              <svg className="h-5 w-5 animate-spin" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              Generating Video…
            </span>
          ) : "✅  Confirm & Generate Video"}
        </button>
      </div>
    </div>
  );
}
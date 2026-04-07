"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { ClipPrompt } from "@/app/page";

/* ─── Types ─────────────────────────────────────────────────────────────── */

interface ClipVerification {
  clip: number;
  status: "approved" | "improved";
  issues: string[];
  improved_prompt: string;
}

interface VerifyResult {
  clips: ClipVerification[];
  overall_score: number;
  summary: string;
}

interface Props {
  clips: ClipPrompt[];
  script: string;
  onAccept: (updatedClips: ClipPrompt[]) => void;
  onSkip: () => void;
  apiBase: string;
  provider?: string;  // "anthropic" | "gemini"
}

/* ─── Component ─────────────────────────────────────────────────────────── */

export default function PromptVerifier({ clips, script, onAccept, onSkip, apiBase, provider = "anthropic" }: Props) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<VerifyResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Track which improved prompts user has accepted (default: accept all)
  const [accepted, setAccepted] = useState<Record<number, boolean>>({});

  const runVerification = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const resp = await fetch(`${apiBase}/api/verify-prompts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clips, script, provider }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || "Verification failed");
      }
      const data: VerifyResult = await resp.json();
      setResult(data);
      // Default: accept all improved prompts
      const defaultAccepted: Record<number, boolean> = {};
      data.clips.forEach((c) => {
        if (c.status === "improved") defaultAccepted[c.clip] = true;
      });
      setAccepted(defaultAccepted);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const handleApply = () => {
    if (!result) return;
    const updatedClips = clips.map((clip) => {
      const verification = result.clips.find((v) => v.clip === clip.clip);
      if (!verification) return clip;
      if (verification.status === "improved" && accepted[clip.clip]) {
        return { ...clip, prompt: verification.improved_prompt };
      }
      return clip;
    });
    onAccept(updatedClips);
  };

  const improvedCount = result?.clips.filter((c) => c.status === "improved").length ?? 0;
  const acceptedCount = Object.values(accepted).filter(Boolean).length;

  const scoreColor =
    !result ? "#888"
    : result.overall_score >= 85 ? "#25a85a"
    : result.overall_score >= 65 ? "#f59e0b"
    : "#ef4444";

  return (
    <div className="space-y-6">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div
        className="rounded-2xl border p-6"
        style={{ background: "rgba(255,255,255,0.04)", borderColor: "rgba(99,102,241,0.3)" }}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <span>✨</span> Gemini Emotional Verification
            </h2>
            <p className="mt-1 text-sm text-white/60">
              Let Gemini enrich your prompts with genuine emotional depth — adds micro-expressions,
              body language cues, voice tone markers, and sensory details that make ads connect.
            </p>
          </div>
          <button
            onClick={onSkip}
            className="shrink-0 rounded-lg border border-white/15 px-4 py-2 text-sm text-white/50 transition hover:bg-white/5 hover:text-white"
          >
            Skip →
          </button>
        </div>

        {/* Run button */}
        {!result && (
          <button
            onClick={runVerification}
            disabled={loading}
            className="mt-5 w-full rounded-xl py-3 text-sm font-bold text-white transition-all hover:opacity-90 hover:-translate-y-0.5 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
            style={{ background: "linear-gradient(90deg, #4338ca, #6366f1)" }}
          >
            {loading ? (
              <>
                <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Gemini is reviewing {clips.length} clips…
              </>
            ) : (
              <>✨ Verify {clips.length} Prompts with Gemini</>
            )}
          </button>
        )}

        {error && (
          <div className="mt-4 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
            ⚠️ {error}
          </div>
        )}
      </div>

      {/* ── Results ────────────────────────────────────────────────────── */}
      <AnimatePresence>
        {result && (
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            className="space-y-4"
          >
            {/* Score banner */}
            <div
              className="rounded-2xl border p-5 flex items-center gap-5"
              style={{ background: "rgba(255,255,255,0.04)", borderColor: "rgba(255,255,255,0.08)" }}
            >
              <div
                className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full text-2xl font-black"
                style={{ background: `${scoreColor}22`, color: scoreColor, border: `2px solid ${scoreColor}55` }}
              >
                {result.overall_score}
              </div>
              <div className="flex-1">
                <p className="text-sm font-semibold text-white">{result.summary}</p>
                <p className="mt-1 text-xs text-white/50">
                  {improvedCount === 0
                    ? "✅ All prompts passed — no changes needed"
                    : `${improvedCount} prompt${improvedCount > 1 ? "s" : ""} improved · ${acceptedCount} selected to apply`}
                </p>
              </div>
              <button
                onClick={runVerification}
                disabled={loading}
                className="shrink-0 rounded-lg border border-white/15 px-3 py-1.5 text-xs text-white/50 transition hover:bg-white/5 hover:text-white disabled:opacity-40"
              >
                🔄 Re-run
              </button>
            </div>

            {/* Per-clip results */}
            {result.clips.map((v, i) => (
              <motion.div
                key={v.clip}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.04 }}
                className="rounded-2xl border overflow-hidden"
                style={{
                  borderColor: v.status === "improved" ? "rgba(245,158,11,0.4)" : "rgba(37,168,90,0.25)",
                  background: "rgba(255,255,255,0.03)",
                }}
              >
                {/* Clip header */}
                <div
                  className="flex items-center gap-3 px-5 py-3"
                  style={{
                    background: v.status === "improved"
                      ? "rgba(245,158,11,0.08)"
                      : "rgba(37,168,90,0.06)",
                  }}
                >
                  <span
                    className="flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold"
                    style={{
                      background: v.status === "improved" ? "rgba(245,158,11,0.2)" : "rgba(37,168,90,0.2)",
                      color: v.status === "improved" ? "#f59e0b" : "#25a85a",
                    }}
                  >
                    {v.clip}
                  </span>
                  <span className="flex-1 text-sm font-medium text-white">
                    Clip {v.clip}
                  </span>
                  <span
                    className="rounded-full px-3 py-0.5 text-xs font-semibold"
                    style={{
                      background: v.status === "improved" ? "rgba(245,158,11,0.15)" : "rgba(37,168,90,0.15)",
                      color: v.status === "improved" ? "#f59e0b" : "#25a85a",
                    }}
                  >
                    {v.status === "improved" ? "⚡ Improved" : "✅ Approved"}
                  </span>
                </div>

                {/* Issues */}
                {v.issues.length > 0 && (
                  <div className="px-5 pt-3 pb-0">
                    <p className="mb-2 text-xs font-semibold text-amber-400/80 uppercase tracking-wide">Issues Found</p>
                    <ul className="space-y-1">
                      {v.issues.map((issue, j) => (
                        <li key={j} className="flex items-start gap-2 text-xs text-white/60">
                          <span className="mt-0.5 text-amber-400">→</span>
                          {issue}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Improved prompt + accept toggle */}
                {v.status === "improved" && (
                  <div className="px-5 pt-4 pb-4">
                    <div className="mb-2 flex items-center justify-between">
                      <p className="text-xs font-semibold text-indigo-400/80 uppercase tracking-wide">
                        Improved Prompt
                      </p>
                      <label className="flex cursor-pointer items-center gap-2">
                        <span className="text-xs text-white/50">
                          {accepted[v.clip] ? "Will apply" : "Will keep original"}
                        </span>
                        <div
                          onClick={() => setAccepted((prev) => ({ ...prev, [v.clip]: !prev[v.clip] }))}
                          className="relative h-5 w-9 cursor-pointer rounded-full transition-colors"
                          style={{ background: accepted[v.clip] ? "#6366f1" : "rgba(255,255,255,0.15)" }}
                        >
                          <div
                            className="absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform"
                            style={{ transform: accepted[v.clip] ? "translateX(16px)" : "translateX(2px)" }}
                          />
                        </div>
                      </label>
                    </div>
                    <textarea
                      readOnly
                      value={v.improved_prompt}
                      rows={8}
                      className="w-full resize-y rounded-xl border border-white/10 bg-white/5 px-4 py-3 font-mono text-xs leading-relaxed text-white/60 outline-none"
                    />
                  </div>
                )}
              </motion.div>
            ))}

            {/* Apply / Skip actions */}
            <div className="flex flex-wrap items-center justify-center gap-4 pt-2">
              <button
                onClick={onSkip}
                className="rounded-lg border border-white/15 px-5 py-2.5 text-sm text-white/50 transition hover:bg-white/5 hover:text-white"
              >
                Keep Original Prompts
              </button>
              <button
                onClick={handleApply}
                className="rounded-xl px-10 py-3 text-sm font-bold text-white transition-all hover:opacity-90 hover:-translate-y-0.5 flex items-center gap-2"
                style={{ background: "linear-gradient(90deg, #4338ca, #6366f1)" }}
              >
                {acceptedCount > 0
                  ? `✅ Apply ${acceptedCount} Fix${acceptedCount > 1 ? "es" : ""} & Continue`
                  : "Continue Without Changes"}
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
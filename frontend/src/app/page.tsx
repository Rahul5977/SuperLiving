"use client";

import { useState, useCallback } from "react";
import { AnimatePresence, motion } from "framer-motion";
import ConfigPanel from "@/components/ConfigPanel";
import PromptEditor from "@/components/PromptEditor";
import PromptVerifier from "@/components/Promptverifier";
import VideoResult from "@/components/VideoResult";
import CharacterUpload from "@/components/CharacterUpload";
import JobsPanel from "@/components/JobsPanel";
import { useJobPoller, type Job } from "@/hooks/useJobPoller";
/* ─── Types ─────────────────────────────────────────────────────────────── */

export interface CharacterAnalysis {
  appearance: string;
  outfit: string;
}

export interface ClipPrompt {
  clip: number;
  scene_summary: string;
  last_frame: string;
  prompt: string;
}

// "verify" is the new phase between "review" and "result"
type Phase = "input" | "review" | "verify" | "result";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/* ─── Page Component ────────────────────────────────────────────────────── */

export default function Home() {
  // ── Phase state ────────────────────────────────────────────────────────
  const [phase, setPhase] = useState<Phase>("input");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ── Config state (Phase 1) ─────────────────────────────────────────────
  const [script, setScript] = useState("");
  const [extraPrompt, setExtraPrompt] = useState("");
  const [numClips, setNumClips] = useState(6);
  const [durationLabel, setDurationLabel] = useState("45s");
  const [aspectRatio, setAspectRatio] = useState("9:16 (Reels / Shorts)");
  const [veoModel, setVeoModel] = useState("veo-3.1-generate-preview");
  const [languageNote, setLanguageNote] = useState(true);

  // ── Character state ────────────────────────────────────────────────────
  const [usePhotos, setUsePhotos] = useState(false);
  const [characters, setCharacters] = useState<
    { name: string; file: File | null }[]
  >([
    { name: "", file: null },
    { name: "", file: null },
  ]);
  const [, setPhotoAnalyses] = useState<Record<string, CharacterAnalysis>>({});

  // ── Prompts state (Phase 2) ────────────────────────────────────────────
  const [clips, setClips] = useState<ClipPrompt[]>([]);
  const [characterSheet, setCharacterSheet] = useState("");

  // ── Result state (Phase 3) ─────────────────────────────────────────────
  const [videoUrl, setVideoUrl] = useState("");
  const [clipPaths, setClipPaths] = useState<string[]>([]);

  /* ─── Phase 1 → Phase 2: Generate Prompts ──────────────────────────── */

  const handleGeneratePrompts = useCallback(async () => {
    if (!script.trim()) {
      setError("Please paste your ad script before generating.");
      return;
    }
    setError(null);
    setLoading(true);

    try {
      let localPhotoAnalyses: Record<string, CharacterAnalysis> = {};

      // Step A: Analyse uploaded character photos
      if (usePhotos) {
        const validChars = characters.filter((c) => c.name.trim() && c.file);
        if (validChars.length > 0) {
          const formData = new FormData();
          validChars.forEach((c) => {
            formData.append("names", c.name.trim());
            formData.append("photos", c.file as File);
          });
          const analysisResp = await fetch(
            `${API_BASE}/api/analyze-characters`,
            {
              method: "POST",
              body: formData,
            },
          );
          if (!analysisResp.ok) {
            const err = await analysisResp.json().catch(() => ({}));
            throw new Error(err.detail || "Character analysis failed");
          }
          const analysisData = await analysisResp.json();
          localPhotoAnalyses = analysisData.analyses || {};
          setPhotoAnalyses(localPhotoAnalyses);
        }
      }

      // Step B: Generate clip prompts
      const arMap: Record<string, string> = {
        "9:16 (Reels / Shorts)": "9:16",
        "16:9 (YouTube / Landscape)": "16:9",
      };
      const resp = await fetch(`${API_BASE}/api/generate-prompts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          script,
          extra_prompt: extraPrompt,
          character_sheet: characterSheet,
          photo_analyses: localPhotoAnalyses,
          aspect_ratio: arMap[aspectRatio] || "9:16",
          num_clips: numClips,
          language_note: languageNote,
          has_photos:
            usePhotos && characters.some((c) => c.name.trim() && c.file),
        }),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || "Prompt generation failed");
      }

      const data = await resp.json();
      setClips(data.clips);
      if (data.character_sheet) setCharacterSheet(data.character_sheet);
      setPhase("review");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [
    script,
    extraPrompt,
    usePhotos,
    characters,
    aspectRatio,
    numClips,
    languageNote,
    characterSheet,
  ]);

  /* ─── Phase 2 → Phase 2.5: Trigger Claude Verify ───────────────────── */

  const handleGoToVerify = useCallback(() => {
    setPhase("verify");
  }, []);

  /* ─── Phase 2.5: Accept verified/improved clips → Phase 3 ──────────── */

  const handleVerifyAccept = useCallback((updatedClips: ClipPrompt[]) => {
    setClips(updatedClips);
    setPhase("review"); // go back to editor with updated prompts
  }, []);

  const handleVerifySkip = useCallback(() => {
    setPhase("review");
  }, []);

  /* ─── Phase 2 → Phase 3: Generate Video ────────────────────────────── */

  const handleGenerateVideo = useCallback(async () => {
    setError(null);
    setLoading(true);

    const arMap: Record<string, string> = {
      "9:16 (Reels / Shorts)": "9:16",
      "16:9 (YouTube / Landscape)": "16:9",
    };

    try {
      const resp = await fetch(`${API_BASE}/api/generate-video`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clips,
          veo_model: veoModel,
          aspect_ratio: arMap[aspectRatio] || "9:16",
          num_clips: numClips,
        }),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || "Video generation failed");
      }

      const data = await resp.json();
      setVideoUrl(`${API_BASE}${data.video_url}`);
      setClipPaths(data.clip_paths);
      setPhase("result");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [clips, veoModel, aspectRatio, numClips]);

  /* ─── Phase 3: Regenerate Selected Clips ───────────────────────────── */

  const handleRegenerate = useCallback(
    async (indices: number[]) => {
      setError(null);
      setLoading(true);

      const arMap: Record<string, string> = {
        "9:16 (Reels / Shorts)": "9:16",
        "16:9 (YouTube / Landscape)": "16:9",
      };

      try {
        const resp = await fetch(`${API_BASE}/api/regenerate-clips`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            clip_indices: indices,
            clips,
            clip_paths: clipPaths,
            veo_model: veoModel,
            aspect_ratio: arMap[aspectRatio] || "9:16",
            num_clips: numClips,
          }),
        });

        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.detail || "Clip regeneration failed");
        }

        const data = await resp.json();
        setVideoUrl(`${API_BASE}${data.video_url}`);
        setClipPaths(data.clip_paths);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    },
    [clips, clipPaths, veoModel, aspectRatio, numClips],
  );

  /* ─── Reset ─────────────────────────────────────────────────────────── */

  const handleReset = () => {
    setPhase("input");
    setClips([]);
    setVideoUrl("");
    setClipPaths([]);
    setError(null);
    setCharacterSheet("");
    setPhotoAnalyses({});
  };
  const handleJobDone = useCallback((job: Job) => {
    if (!job.result) return;
    setVideoUrl(`${API_BASE}${job.result.video_url}`);
    setClipPaths(job.result.clip_paths);
    setPhase("result");
    setLoading(false);
  }, []);

  const handleJobError = useCallback((job: Job) => {
    setError(job.error ?? "Generation failed. Please try again.");
    setLoading(false);
  }, []);

  const { jobs, activeJobId, addJob, setActive } = useJobPoller({
    apiBase: API_BASE,
    onJobDone: handleJobDone,
    onJobError: handleJobError,
  });

  const handleOpenJob = useCallback(
    (job: Job) => {
      if (job.status !== "done" || !job.result) return;
      setVideoUrl(`${API_BASE}${job.result.video_url}`);
      setClipPaths(job.result.clip_paths);
      setActive(job.id);
      setPhase("result");
    },
    [setActive],
  );

  /* ─── Render ────────────────────────────────────────────────────────── */

  return (
    <main className="min-h-screen">
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="mx-auto max-w-7xl px-4 pt-6 sm:px-6 lg:px-8">
        <div
          className="mb-8 flex items-center gap-4 rounded-2xl px-6 py-5"
          style={{
            background: "linear-gradient(90deg, #1a7a3c, #25a85a)",
            boxShadow: "0 4px 24px rgba(26,122,60,0.35)",
          }}
        >
          <span className="text-4xl">🎬</span>
          <div>
            <h1 className="text-2xl font-bold text-white">
              SuperLiving — Ad Generator
            </h1>
            <p className="mt-0.5 text-sm text-white/80">
              Transform your scripts into high-impact video ads for Tier 3 &amp;
              4 India · Powered by AI
            </p>
          </div>
        </div>

        {/* ── Phase Progress Bar ───────────────────────────────────────── */}
        <div className="mb-6 flex items-center gap-2">
          {(["input", "review", "verify", "result"] as Phase[]).map((p, i) => {
            const labels: Record<Phase, string> = {
              input: "Script",
              review: "Edit Prompts",
              verify: "Claude Review",
              result: "Video",
            };
            const phaseOrder: Phase[] = ["input", "review", "verify", "result"];
            const currentIndex = phaseOrder.indexOf(phase);
            const thisIndex = phaseOrder.indexOf(p);
            const isActive = p === phase;
            const isDone = thisIndex < currentIndex;
            return (
              <div key={p} className="flex items-center gap-2 flex-1">
                <div
                  className="flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-all"
                  style={{
                    background: isActive
                      ? p === "verify"
                        ? "rgba(99,102,241,0.2)"
                        : "rgba(37,168,90,0.2)"
                      : isDone
                        ? "rgba(37,168,90,0.1)"
                        : "rgba(255,255,255,0.05)",
                    color: isActive
                      ? p === "verify"
                        ? "#818cf8"
                        : "#25a85a"
                      : isDone
                        ? "#25a85a"
                        : "rgba(255,255,255,0.3)",
                    border: isActive
                      ? p === "verify"
                        ? "1px solid rgba(99,102,241,0.4)"
                        : "1px solid rgba(37,168,90,0.4)"
                      : "1px solid transparent",
                  }}
                >
                  <span>{isDone ? "✓" : i + 1}</span>
                  <span>{labels[p]}</span>
                </div>
                {i < 3 && (
                  <div
                    className="h-px flex-1"
                    style={{
                      background: isDone
                        ? "rgba(37,168,90,0.3)"
                        : "rgba(255,255,255,0.08)",
                    }}
                  />
                )}
              </div>
            );
          })}
        </div>

        {/* ── Error Banner ──────────────────────────────────────────────── */}
        <AnimatePresence>
          {error && (
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="mb-6 rounded-xl border border-red-500/30 bg-red-500/10 px-5 py-3 text-red-300"
            >
              ⚠️ {error}
              <button
                onClick={() => setError(null)}
                className="ml-3 text-red-400 hover:text-red-200"
              >
                ✕
              </button>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Phase Router ──────────────────────────────────────────────── */}
        <div className="grid gap-6 lg:grid-cols-[1fr_300px]">
          <div>
            <AnimatePresence mode="wait">
              {phase === "input" && (
                <motion.div
                  key="input"
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 20 }}
                  transition={{ duration: 0.3 }}
                >
                  <div className="grid gap-8 lg:grid-cols-5">
                    <div className="lg:col-span-3">
                      <ConfigPanel
                        script={script}
                        setScript={setScript}
                        extraPrompt={extraPrompt}
                        setExtraPrompt={setExtraPrompt}
                        numClips={numClips}
                        setNumClips={setNumClips}
                        durationLabel={durationLabel}
                        setDurationLabel={setDurationLabel}
                        aspectRatio={aspectRatio}
                        setAspectRatio={setAspectRatio}
                        veoModel={veoModel}
                        setVeoModel={setVeoModel}
                        languageNote={languageNote}
                        setLanguageNote={setLanguageNote}
                      />
                    </div>
                    <div className="lg:col-span-2">
                      <CharacterUpload
                        usePhotos={usePhotos}
                        setUsePhotos={setUsePhotos}
                        characters={characters}
                        setCharacters={setCharacters}
                      />
                    </div>
                  </div>

                  <div className="mt-8 flex justify-center">
                    <button
                      onClick={handleGeneratePrompts}
                      disabled={loading}
                      className="rounded-xl px-12 py-4 text-lg font-bold text-white transition-all hover:opacity-90 hover:-translate-y-0.5 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                      style={{
                        background: "linear-gradient(90deg, #1a7a3c, #25a85a)",
                      }}
                    >
                      {loading ? (
                        <span className="flex items-center gap-2">
                          <svg
                            className="h-5 w-5 animate-spin"
                            viewBox="0 0 24 24"
                          >
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
                          Generating Prompts…
                        </span>
                      ) : (
                        "🎬  Generate Prompts"
                      )}
                    </button>
                  </div>
                </motion.div>
              )}

              {phase === "review" && (
                <motion.div
                  key="review"
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 20 }}
                  transition={{ duration: 0.3 }}
                >
                  <PromptEditor
                    clips={clips}
                    setClips={setClips}
                    characterSheet={characterSheet}
                    setCharacterSheet={setCharacterSheet}
                    onVerify={handleGoToVerify}
                    onConfirm={handleGenerateVideo}
                    onBack={() => setPhase("input")}
                    loading={loading}
                  />
                </motion.div>
              )}

              {phase === "verify" && (
                <motion.div
                  key="verify"
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 20 }}
                  transition={{ duration: 0.3 }}
                >
                  <PromptVerifier
                    clips={clips}
                    script={script}
                    onAccept={handleVerifyAccept}
                    onSkip={handleVerifySkip}
                    apiBase={API_BASE}
                  />
                </motion.div>
              )}

              {phase === "result" && (
                <motion.div
                  key="result"
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 20 }}
                  transition={{ duration: 0.3 }}
                >
                  <VideoResult
                    videoUrl={videoUrl}
                    clips={clips}
                    setClips={setClips}
                    numClips={numClips}
                    onRegenerate={handleRegenerate}
                    onReset={handleReset}
                    loading={loading}
                  />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
          <aside className="lg:sticky lg:top-6 lg:self-start">
            <JobsPanel
              jobs={jobs}
              activeJobId={activeJobId}
              onOpenJob={handleOpenJob}
            />
          </aside>
        </div>

        {/* ── Footer ────────────────────────────────────────────────────── */}
        <p className="mt-12 pb-8 text-center text-xs text-[#555]">
          SuperLiving Internal Tool · AI-Powered Ad Generator · 8s max per clip
        </p>
      </div>
    </main>
  );
}

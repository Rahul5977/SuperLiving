"use client";

import { useState, useCallback, useEffect } from "react";
import { AnimatePresence, motion } from "framer-motion";
import ConfigPanel from "@/components/ConfigPanel";
import PromptEditor from "@/components/PromptEditor";
import VideoResult from "@/components/VideoResult";
import CharacterUpload from "@/components/CharacterUpload";
import JobsPanel from "@/components/JobsPanel";
import { api, type ClipPrompt, type CharacterAnalysis } from "@/lib/api";
import { useJobPoller, type Job } from "@/hooks/useJobPoller";

// ── Constants ──────────────────────────────────────────────────────────────

const AR_MAP: Record<string, string> = {
  "9:16 (Reels / Shorts)": "9:16",
  "16:9 (YouTube / Landscape)": "16:9",
};

// Re-export for components that need them
export type { ClipPrompt };
export type { Job };

type Phase = "input" | "review" | "result";

// ── Page ───────────────────────────────────────────────────────────────────

export default function Home() {
  // ── Phase ────────────────────────────────────────────────────────────────
  const [phase, setPhase] = useState<Phase>("input");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ── Config ───────────────────────────────────────────────────────────────
  const [script, setScript] = useState("");
  const [extraPrompt, setExtraPrompt] = useState("");
  const [numClips, setNumClips] = useState(6);
  const [durationLabel, setDurationLabel] = useState("45s");
  const [aspectRatio, setAspectRatio] = useState("9:16 (Reels / Shorts)");
  const [veoModel, setVeoModel] = useState("veo-3.1-generate-preview");
  const [languageNote, setLanguageNote] = useState(true);

  // ── Characters ───────────────────────────────────────────────────────────
  const [usePhotos, setUsePhotos] = useState(false);
  const [characters, setCharacters] = useState<{ name: string; file: File | null }[]>([
    { name: "", file: null },
    { name: "", file: null },
  ]);
  const [photoAnalyses, setPhotoAnalyses] = useState<Record<string, CharacterAnalysis>>({});

  // ── Prompts ──────────────────────────────────────────────────────────────
  const [clips, setClips] = useState<ClipPrompt[]>([]);
  const [characterSheet, setCharacterSheet] = useState("");

  // ── Result ───────────────────────────────────────────────────────────────
  const [videoUrl, setVideoUrl] = useState("");
  const [clipPaths, setClipPaths] = useState<string[]>([]);

  // ── Jobs (parallel workers) ───────────────────────────────────────────────
  const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

  const handleJobDone = useCallback((job: Job) => {
    if (!job.result) return;
    setVideoUrl(api.videoUrl(job.result.video_url));
    setClipPaths(job.result.clip_paths);
    setPhase("result");
    setLoading(false);
  }, []);

  const handleJobError = useCallback((job: Job) => {
    setError(job.error ?? "Generation failed. Please try again.");
    setLoading(false);
  }, []);

  const { jobs, activeJob, addJob, setActive } = useJobPoller({
    apiBase,
    onJobDone: handleJobDone,
    onJobError: handleJobError,
  });

  // ── Phase 1 → Phase 2: Generate prompts ──────────────────────────────────

  const handleGeneratePrompts = useCallback(async () => {
    if (!script.trim()) {
      setError("Please paste your ad script before generating.");
      return;
    }
    setError(null);
    setLoading(true);

    try {
      let analyses: Record<string, CharacterAnalysis> = {};

      if (usePhotos) {
        const validChars = characters.filter((c) => c.name.trim() && c.file);
        if (validChars.length > 0) {
          const result = await api.analyzeCharacters(
            validChars.map((c) => c.name.trim()),
            validChars.map((c) => c.file!)
          );
          analyses = result.analyses;
          setPhotoAnalyses(analyses);
        }
      }

      const data = await api.generatePrompts({
        script,
        extra_prompt: extraPrompt,
        photo_analyses: analyses,
        aspect_ratio: aspectRatio,
        num_clips: numClips,
        language_note: languageNote,
        has_photos: usePhotos && Object.keys(analyses).length > 0,
      });

      setClips(data.clips);
      setCharacterSheet(data.character_sheet ?? "");
      setPhase("review");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [script, extraPrompt, usePhotos, characters, aspectRatio, numClips, languageNote]);

  // ── Phase 2 → Phase 3: Enqueue video generation job ─────────────────────

  const handleGenerateVideo = useCallback(async () => {
    setError(null);
    setLoading(true);

    try {
      const { job_id } = await api.generateVideo({
        clips,
        veo_model: veoModel,
        aspect_ratio: AR_MAP[aspectRatio] ?? "9:16",
        num_clips: numClips,
      });

      addJob({
        id: job_id,
        label: `Ad · ${numClips} clips · ${new Date().toLocaleTimeString()}`,
        status: "pending",
        step: "Queued…",
        progress: 0,
        result: null,
        error: null,
        createdAt: Date.now(),
      });
      // loading stays true — cleared by onJobDone / onJobError
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
      setLoading(false);
    }
  }, [clips, veoModel, aspectRatio, numClips, addJob]);

  // ── Phase 3: Regenerate selected clips ────────────────────────────────────

  const handleRegenerate = useCallback(
    async (indices: number[]) => {
      setError(null);
      setLoading(true);

      try {
        const { job_id } = await api.regenerateClips({
          clip_indices: indices,
          clips,
          clip_paths: clipPaths,
          veo_model: veoModel,
          aspect_ratio: AR_MAP[aspectRatio] ?? "9:16",
          num_clips: numClips,
        });

        addJob({
          id: job_id,
          label: `Regen clips ${indices.map((i) => i + 1).join(",")} · ${new Date().toLocaleTimeString()}`,
          status: "pending",
          step: "Queued…",
          progress: 0,
          result: null,
          error: null,
          createdAt: Date.now(),
        });
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Unknown error");
        setLoading(false);
      }
    },
    [clips, clipPaths, veoModel, aspectRatio, numClips, addJob]
  );

  // ── Open a completed job from the sidebar ─────────────────────────────────

  const handleOpenJob = useCallback(
    (job: Job) => {
      if (job.status !== "done" || !job.result) return;
      setVideoUrl(api.videoUrl(job.result.video_url));
      setClipPaths(job.result.clip_paths);
      setActive(job.id);
      setPhase("result");
    },
    [setActive]
  );

  // ── Reset ─────────────────────────────────────────────────────────────────

  const handleReset = useCallback(() => {
    setPhase("input");
    setClips([]);
    setVideoUrl("");
    setClipPaths([]);
    setError(null);
    setCharacterSheet("");
    setPhotoAnalyses({});
    setLoading(false);
    setActive(null);
  }, [setActive]);

  // ── Stats for header badge ─────────────────────────────────────────────────
  const runningJobs = jobs.filter((j) =>
    ["pending", "generating", "stitching"].includes(j.status)
  ).length;
  const doneJobs = jobs.filter((j) => j.status === "done").length;

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <main className="min-h-screen">
      <div className="mx-auto max-w-7xl px-4 pt-6 pb-20 sm:px-6 lg:px-8">

        {/* Header */}
        <motion.header
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] as [number, number, number, number] }}
          className="mb-8 flex items-center justify-between"
        >
          <div className="flex items-center gap-4">
            <div
              className="flex h-11 w-11 items-center justify-center rounded-2xl text-xl"
              style={{
                background: "linear-gradient(135deg, var(--accent) 0%, var(--accent-2) 100%)",
                boxShadow: "0 0 24px var(--accent-glow)",
              }}
            >
              🎬
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight">
                SuperLiving{" "}
                <span className="gradient-text">Ad Generator</span>
              </h1>
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                Veo · Gemini · 4 parallel workers
              </p>
            </div>
          </div>

          {/* Live job badge */}
          <AnimatePresence>
            {jobs.length > 0 && (
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }}
                className="glass flex items-center gap-2 rounded-2xl px-4 py-2"
              >
                <span
                  className="h-2 w-2 rounded-full"
                  style={{
                    background: runningJobs > 0 ? "var(--accent)" : "var(--success)",
                    boxShadow: runningJobs > 0 ? "0 0 6px var(--accent-glow)" : "none",
                    animation: runningJobs > 0 ? "pulse-dot 1.5s ease-in-out infinite" : "none",
                  }}
                />
                <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
                  {runningJobs > 0 ? `${runningJobs} generating` : `${doneJobs} done`}
                </span>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.header>

        {/* Error banner */}
        <AnimatePresence>
          {error && (
            <motion.div
              initial={{ opacity: 0, y: -8, height: 0 }}
              animate={{ opacity: 1, y: 0, height: "auto" }}
              exit={{ opacity: 0, y: -8, height: 0 }}
              className="mb-5 flex items-center gap-3 overflow-hidden rounded-xl px-4 py-3"
              style={{
                background: "rgba(244,63,94,0.08)",
                border: "1px solid rgba(244,63,94,0.28)",
                color: "#fb7185",
              }}
            >
              <span className="text-sm flex-1">⚠️ {error}</span>
              <button
                onClick={() => setError(null)}
                className="flex-shrink-0 text-xs opacity-50 hover:opacity-100 transition-opacity"
              >
                ✕
              </button>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Main layout */}
        <div className="grid gap-6 lg:grid-cols-[1fr_300px]">

          {/* Content area */}
          <div>
            <AnimatePresence mode="wait">

              {/* ── INPUT PHASE ──────────────────────────────────────── */}
              {phase === "input" && (
                <motion.div
                  key="input"
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 20 }}
                  transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] as [number, number, number, number] }}
                  className="space-y-6"
                >
                  <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
                    <ConfigPanel
                      script={script} setScript={setScript}
                      extraPrompt={extraPrompt} setExtraPrompt={setExtraPrompt}
                      numClips={numClips} setNumClips={setNumClips}
                      durationLabel={durationLabel} setDurationLabel={setDurationLabel}
                      aspectRatio={aspectRatio} setAspectRatio={setAspectRatio}
                      veoModel={veoModel} setVeoModel={setVeoModel}
                      languageNote={languageNote} setLanguageNote={setLanguageNote}
                    />
                    <CharacterUpload
                      usePhotos={usePhotos} setUsePhotos={setUsePhotos}
                      characters={characters} setCharacters={setCharacters}
                    />
                  </div>

                  <div className="flex justify-center pt-2">
                    <button
                      onClick={handleGeneratePrompts}
                      disabled={loading}
                      className="btn-primary rounded-2xl px-12 py-4 text-base font-semibold tracking-wide"
                    >
                      {loading ? (
                        <CyclingLoader
                          steps={["Analyzing script…", "Parsing characters…", "Building prompts…", "Almost ready…"]}
                        />
                      ) : (
                        "✦ Generate Prompts"
                      )}
                    </button>
                  </div>
                </motion.div>
              )}

              {/* ── REVIEW PHASE ─────────────────────────────────────── */}
              {phase === "review" && (
                <motion.div
                  key="review"
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 20 }}
                  transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] as [number, number, number, number] }}
                >
                  <PromptEditor
                    clips={clips} setClips={setClips}
                    characterSheet={characterSheet} setCharacterSheet={setCharacterSheet}
                    onConfirm={handleGenerateVideo}
                    onBack={() => setPhase("input")}
                    loading={loading}
                    activeJob={activeJob}
                  />
                </motion.div>
              )}

              {/* ── RESULT PHASE ─────────────────────────────────────── */}
              {phase === "result" && (
                <motion.div
                  key="result"
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 20 }}
                  transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] as [number, number, number, number] }}
                >
                  <VideoResult
                    videoUrl={videoUrl} clips={clips}
                    setClips={setClips} numClips={numClips}
                    onRegenerate={handleRegenerate} onReset={handleReset}
                    loading={loading} activeJob={activeJob}
                  />
                </motion.div>
              )}

            </AnimatePresence>
          </div>

          {/* Sidebar */}
          <aside className="lg:sticky lg:top-6 lg:self-start">
            <JobsPanel
              jobs={jobs}
              activeJobId={activeJob?.id ?? null}
              onOpenJob={handleOpenJob}
            />
          </aside>
        </div>

        <p className="mt-16 text-center text-xs" style={{ color: "var(--text-muted)" }}>
          SuperLiving Internal Tool · AI-Powered · Up to 4 parallel jobs
        </p>
      </div>
    </main>
  );
}

// ── Cycling loading text component ────────────────────────────────────────

function CyclingLoader({ steps }: { steps: string[] }) {
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    const t = setInterval(() => setIdx((i) => (i + 1) % steps.length), 1800);
    return () => clearInterval(t);
  }, [steps.length]);

  return (
    <span className="flex items-center gap-2.5">
      {/* Animated dots */}
      <span className="flex gap-1">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="block h-1.5 w-1.5 rounded-full bg-white/60"
            style={{ animation: `pulse-dot 1.2s ease-in-out ${i * 0.2}s infinite` }}
          />
        ))}
      </span>
      {/* Cycling text */}
      <AnimatePresence mode="wait">
        <motion.span
          key={idx}
          initial={{ opacity: 0, y: 5 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -5 }}
          transition={{ duration: 0.18 }}
        >
          {steps[idx]}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}
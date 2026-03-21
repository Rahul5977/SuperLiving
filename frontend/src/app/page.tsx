"use client";

import { useState, useCallback, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";
import ConfigPanel from "@/components/ConfigPanel";
import PromptEditor from "@/components/PromptEditor";
import PromptVerifier from "@/components/Promptverifier";
import VideoResult from "@/components/VideoResult";
import CharacterUpload from "@/components/CharacterUpload";
import JobsPanel from "@/components/JobsPanel";
import { useJobPoller } from "@/hooks/useJobPoller";
import type { Job } from "@/hooks/useJobPoller";

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
  const [characters, setCharacters] = useState<{ name: string; file: File | null }[]>([
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

  // ── Job system ────────────────────────────────────────────────────────
  const handleJobDone = useCallback((job: Job) => {
    if (job.result) {
      setVideoUrl(`${API_BASE}${job.result.video_url}`);
      setClipPaths(job.result.clip_paths);
      setPhase("result");
    }
  }, []);

  const { jobs, activeJobId, addJob, setActive } = useJobPoller({
    apiBase: API_BASE,
    onJobDone: handleJobDone,
  });

  const handleOpenJob = useCallback(
    (job: Job) => {
      if (job.result) {
        setVideoUrl(`${API_BASE}${job.result.video_url}`);
        setClipPaths(job.result.clip_paths);
        setActive(job.id);
        setPhase("result");
      }
    },
    [setActive]
  );

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
          const analysisResp = await fetch(`${API_BASE}/api/analyze-characters`, {
            method: "POST",
            body: formData,
          });
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
          has_photos: usePhotos && characters.some((c) => c.name.trim() && c.file),
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
  }, [script, extraPrompt, usePhotos, characters, aspectRatio, numClips, languageNote, characterSheet]);

  /* ─── Phase 2 → Phase 2.5: Trigger Claude Verify ───────────────────── */

  const handleGoToVerify = useCallback(() => {
    setPhase("verify");
  }, []);

  /* ─── Phase 2.5: Accept verified/improved clips → Phase 3 ──────────── */

  const handleVerifyAccept = useCallback((updatedClips: ClipPrompt[]) => {
    setClips(updatedClips);
    setPhase("review");
  }, []);

  const handleVerifySkip = useCallback(() => {
    setPhase("review");
  }, []);

  /* ─── Phase 2 → Phase 3: Generate Video (async) ────────────────────── */

  const handleGenerateVideo = useCallback(async () => {
    setError(null);
    setLoading(true);

    const arMap: Record<string, string> = {
      "9:16 (Reels / Shorts)": "9:16",
      "16:9 (YouTube / Landscape)": "16:9",
    };

    try {
      const resp = await fetch(`${API_BASE}/api/generate-video-async`, {
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
      addJob({
        id:        data.job_id,
        label:     `Job #${jobs.length + 1} — ${numClips} clips`,
        status:    "pending",
        step:      "Queued…",
        progress:  0,
        result:    null,
        error:     null,
        createdAt: Date.now(),
      });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [clips, veoModel, aspectRatio, numClips, jobs.length, addJob]);

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
        const resp = await fetch(`${API_BASE}/api/regenerate-clips-async`, {
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
        addJob({
          id:        data.job_id,
          label:     `Regen #${jobs.length + 1} — clips ${indices.map((i) => i + 1).join(", ")}`,
          status:    "pending",
          step:      "Queued…",
          progress:  0,
          result:    null,
          error:     null,
          createdAt: Date.now(),
        });
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    },
    [clips, clipPaths, veoModel, aspectRatio, numClips, jobs.length, addJob]
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

  /* ─── Download / Upload Prompts ────────────────────────────────────── */

  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDownloadPrompts = useCallback(() => {
    if (clips.length === 0) return;
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
  }, [clips]);

  const handleUploadFile = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (ev) => {
        const text = ev.target?.result as string;
        if (!text) return;

        // Normalize line endings (Windows \r\n → \n)
        const normalized = text.replace(/\r\n/g, "\n");

        const hasClipHeaders = /^CLIP\s+\d+/im.test(normalized);
        const hasSeparators = /\n\s*---\s*\n/.test(normalized);

        // ── If file has CLIP headers or --- separators → parse as prompts ──
        if (hasClipHeaders || hasSeparators) {
          let blocks: string[];

          if (hasSeparators) {
            blocks = normalized.split(/\n\s*---\s*\n/).map((b) => b.trim()).filter(Boolean);
          } else {
            blocks = normalized.split(/(?=^CLIP\s+\d+)/im).map((b) => b.trim()).filter(Boolean);
          }

          const parsed: ClipPrompt[] = blocks.map((block, i) => {
            const headerMatch = block.match(/^CLIP\s+(\d+)\s*[—\-–]\s*(.+)\n([\s\S]*)$/);
            if (headerMatch) {
              return {
                clip: parseInt(headerMatch[1], 10),
                scene_summary: headerMatch[2].trim(),
                last_frame: "",
                prompt: headerMatch[3].trim(),
              };
            }
            return {
              clip: i + 1,
              scene_summary: `Clip ${i + 1}`,
              last_frame: "",
              prompt: block,
            };
          });
          if (parsed.length > 0) {
            setClips(parsed);
            setNumClips(parsed.length);
            setPhase("review");
          }
        }
        // ── Raw script file → load into script textarea, stay on input phase ──
        else {
          setScript(normalized.trim());
          setPhase("input");
        }
      };
      reader.readAsText(file);
      // Reset so the same file can be re-uploaded
      e.target.value = "";
    },
    [setClips, setNumClips, setScript]
  );

  /* ─── Render ────────────────────────────────────────────────────────── */

  const showJobsPanel = true

  return (
    <main className="min-h-screen">
      <div className="mx-auto max-w-7xl px-4 pt-6 sm:px-6 lg:px-8">
        {/* ── Header ──────────────────────────────────────────────────────── */}
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
              Transform your scripts into high-impact video ads for Tier 3 &amp; 4 India · Powered by AI
            </p>
          </div>
        </div>

        {/* ── Phase Progress Bar ───────────────────────────────────────── */}
        <div className="mb-6 flex items-center gap-2">
          {(["input", "review", "verify", "result"] as Phase[]).map((p, i) => {
            const labels: Record<Phase, string> = {
              input: "Script",
              review: "Edit Prompts",
              verify: "Gemini Review",
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
                      ? p === "verify" ? "rgba(99,102,241,0.2)" : "rgba(37,168,90,0.2)"
                      : isDone ? "rgba(37,168,90,0.1)" : "rgba(255,255,255,0.05)",
                    color: isActive
                      ? p === "verify" ? "#818cf8" : "#25a85a"
                      : isDone ? "#25a85a" : "rgba(255,255,255,0.3)",
                    border: isActive
                      ? p === "verify" ? "1px solid rgba(99,102,241,0.4)" : "1px solid rgba(37,168,90,0.4)"
                      : "1px solid transparent",
                  }}
                >
                  <span>{isDone ? "✓" : i + 1}</span>
                  <span>{labels[p]}</span>
                </div>
                {i < 3 && (
                  <div
                    className="h-px flex-1"
                    style={{ background: isDone ? "rgba(37,168,90,0.3)" : "rgba(255,255,255,0.08)" }}
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
              <button onClick={() => setError(null)} className="ml-3 text-red-400 hover:text-red-200">✕</button>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Persistent Download / Upload toolbar ─────────────────────── */}
        <div className="mb-6 flex items-center gap-3">
          <button
            onClick={handleDownloadPrompts}
            disabled={clips.length === 0}
            className="rounded-lg border border-white/15 px-4 py-2 text-sm text-white/60 transition hover:bg-white/5 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed flex items-center gap-1.5"
          >
            <span>⬇️</span> Download Prompts
          </button>
          <button
            onClick={() => fileInputRef.current?.click()}
            className="rounded-lg border border-white/15 px-4 py-2 text-sm text-white/60 transition hover:bg-white/5 hover:text-white flex items-center gap-1.5"
          >
            <span>⬆️</span> Upload Script / Prompts
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".txt"
            onChange={handleUploadFile}
            className="hidden"
          />
          {clips.length > 0 && (
            <span className="text-xs text-white/30 ml-1">
              {clips.length} clip{clips.length !== 1 ? "s" : ""} loaded
            </span>
          )}
        </div>

        {/* ── Main layout: content + jobs sidebar ───────────────────────── */}
        <div className={showJobsPanel ? "grid gap-6 lg:grid-cols-6" : ""}>

          {/* ── Phase Router ────────────────────────────────────────────── */}
          <div className={showJobsPanel ? "lg:col-span-4" : ""}>
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
                      style={{ background: "linear-gradient(90deg, #1a7a3c, #25a85a)" }}
                    >
                      {loading ? (
                        <span className="flex items-center gap-2">
                          <svg className="h-5 w-5 animate-spin" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                          </svg>
                          Generating Prompts…
                        </span>
                      ) : "🎬  Generate Prompts"}
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

          {/* ── Jobs sidebar ─────────────────────────────────────────────── */}
          {showJobsPanel && (
            <div className="lg:col-span-2">
              <div
                className="sticky top-6 rounded-2xl p-4"
                style={{
                  background: "rgba(255,255,255,0.03)",
                  border: "1px solid rgba(255,255,255,0.08)",
                }}
              >
                <JobsPanel
                  jobs={jobs}
                  activeJobId={activeJobId}
                  onOpenJob={handleOpenJob}
                />
              </div>
            </div>
          )}

        </div>

        {/* ── Footer ────────────────────────────────────────────────────── */}
        <p className="mt-12 pb-8 text-center text-xs text-[#555]">
          SuperLiving Internal Tool · AI-Powered Ad Generator · 8s max per clip
        </p>
      </div>
    </main>
  );
}

"use client";

import { useState, useCallback, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";
import ConfigPanel from "@/components/ConfigPanel";
import PromptEditor from "@/components/PromptEditor";
import VideoResult from "@/components/VideoResult";
import CharacterUpload from "@/components/CharacterUpload";

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

type Phase = "input" | "review" | "result";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/* ─── Worker Session Type ───────────────────────────────────────────────── */

interface WorkerSession {
  id: string;
  label: string;
  phase: Phase;
  loading: boolean;
  error: string | null;
  // config
  script: string;
  extraPrompt: string;
  numClips: number;
  durationLabel: string;
  aspectRatio: string;
  veoModel: string;
  languageNote: boolean;
  // characters
  usePhotos: boolean;
  characters: { name: string; file: File | null }[];
  photoAnalyses: Record<string, CharacterAnalysis>;
  // prompts
  clips: ClipPrompt[];
  characterSheet: string;
  // result
  videoUrl: string;
  clipPaths: string[];
}

function createSession(id: string, label: string): WorkerSession {
  return {
    id,
    label,
    phase: "input",
    loading: false,
    error: null,
    script: "",
    extraPrompt: "",
    numClips: 6,
    durationLabel: "45s",
    aspectRatio: "9:16 (Reels / Shorts)",
    veoModel: "veo-3.1-generate-preview",
    languageNote: true,
    usePhotos: false,
    characters: [
      { name: "", file: null },
      { name: "", file: null },
    ],
    photoAnalyses: {},
    clips: [],
    characterSheet: "",
    videoUrl: "",
    clipPaths: [],
  };
}

let sessionCounter = 1;

/* ─── Page Component ────────────────────────────────────────────────────── */

export default function Home() {
  const [sessions, setSessions] = useState<WorkerSession[]>([
    createSession("s1", "Worker 1"),
  ]);
  const [activeSessionId, setActiveSessionId] = useState<string>("s1");

  const activeSession = sessions.find((s) => s.id === activeSessionId)!;

  /* ── Session helpers ─────────────────────────────────────────────────── */

  const updateSession = useCallback(
    (id: string, patch: Partial<WorkerSession>) => {
      setSessions((prev) =>
        prev.map((s) => (s.id === id ? { ...s, ...patch } : s))
      );
    },
    []
  );

  const addWorker = () => {
    sessionCounter += 1;
    const id = `s${sessionCounter}`;
    const label = `Worker ${sessionCounter}`;
    setSessions((prev) => [...prev, createSession(id, label)]);
    setActiveSessionId(id);
  };

  const removeWorker = (id: string) => {
    setSessions((prev) => {
      const remaining = prev.filter((s) => s.id !== id);
      if (remaining.length === 0) return prev; // keep at least one
      return remaining;
    });
    setSessions((prev) => {
      if (activeSessionId === id && prev.length > 0) {
        setActiveSessionId(prev[0].id);
      }
      return prev;
    });
  };

  /* ── Phase 1 → Phase 2: Generate Prompts ───────────────────────────── */

  const handleGeneratePrompts = useCallback(
    async (sessionId: string) => {
      const s = sessions.find((x) => x.id === sessionId)!;
      if (!s.script.trim()) {
        updateSession(sessionId, {
          error: "Please paste your ad script before generating.",
        });
        return;
      }
      updateSession(sessionId, { error: null, loading: true });

      try {
        let analyses: Record<string, CharacterAnalysis> = {};
        if (s.usePhotos) {
          const validChars = s.characters.filter(
            (c) => c.name.trim() && c.file
          );
          if (validChars.length > 0) {
            const formData = new FormData();
            for (const c of validChars) {
              formData.append("names", c.name.trim());
              formData.append("photos", c.file!);
            }
            const resp = await fetch(`${API_BASE}/api/analyze-characters`, {
              method: "POST",
              body: formData,
            });
            if (!resp.ok) {
              const err = await resp.json().catch(() => ({}));
              throw new Error(err.detail || "Character analysis failed");
            }
            const data = await resp.json();
            analyses = data.analyses;
            updateSession(sessionId, { photoAnalyses: analyses });
          }
        }

        const resp = await fetch(`${API_BASE}/api/generate-prompts`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            script: s.script,
            extra_prompt: s.extraPrompt,
            photo_analyses: analyses,
            aspect_ratio: s.aspectRatio,
            num_clips: s.numClips,
            language_note: s.languageNote,
            has_photos: s.usePhotos && Object.keys(analyses).length > 0,
          }),
        });

        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.detail || "Prompt generation failed");
        }

        const data = await resp.json();
        updateSession(sessionId, {
          clips: data.clips,
          characterSheet: data.character_sheet || "",
          phase: "review",
        });
      } catch (e: unknown) {
        updateSession(sessionId, {
          error: e instanceof Error ? e.message : "Unknown error",
        });
      } finally {
        updateSession(sessionId, { loading: false });
      }
    },
    [sessions, updateSession]
  );

  /* ── Phase 2 → Phase 3: Generate Video ─────────────────────────────── */

  const handleGenerateVideo = useCallback(
    async (sessionId: string) => {
      const s = sessions.find((x) => x.id === sessionId)!;
      updateSession(sessionId, { error: null, loading: true });

      const arMap: Record<string, string> = {
        "9:16 (Reels / Shorts)": "9:16",
        "16:9 (YouTube / Landscape)": "16:9",
      };

      try {
        const resp = await fetch(`${API_BASE}/api/generate-video`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            clips: s.clips,
            veo_model: s.veoModel,
            aspect_ratio: arMap[s.aspectRatio] || "9:16",
            num_clips: s.numClips,
          }),
        });

        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.detail || "Video generation failed");
        }

        const data = await resp.json();
        updateSession(sessionId, {
          videoUrl: `${API_BASE}${data.video_url}`,
          clipPaths: data.clip_paths,
          phase: "result",
        });
      } catch (e: unknown) {
        updateSession(sessionId, {
          error: e instanceof Error ? e.message : "Unknown error",
        });
      } finally {
        updateSession(sessionId, { loading: false });
      }
    },
    [sessions, updateSession]
  );

  /* ── Phase 3: Regenerate Selected Clips ─────────────────────────────── */

  const handleRegenerate = useCallback(
    async (sessionId: string, indices: number[]) => {
      const s = sessions.find((x) => x.id === sessionId)!;
      updateSession(sessionId, { error: null, loading: true });

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
            clips: s.clips,
            clip_paths: s.clipPaths,
            veo_model: s.veoModel,
            aspect_ratio: arMap[s.aspectRatio] || "9:16",
            num_clips: s.numClips,
          }),
        });

        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.detail || "Clip regeneration failed");
        }

        const data = await resp.json();
        updateSession(sessionId, {
          videoUrl: `${API_BASE}${data.video_url}`,
          clipPaths: data.clip_paths,
        });
      } catch (e: unknown) {
        updateSession(sessionId, {
          error: e instanceof Error ? e.message : "Unknown error",
        });
      } finally {
        updateSession(sessionId, { loading: false });
      }
    },
    [sessions, updateSession]
  );

  /* ── Reset session ────────────────────────────────────────────────────── */

  const handleReset = (sessionId: string) => {
    setSessions((prev) =>
      prev.map((s) =>
        s.id === sessionId
          ? {
              ...createSession(s.id, s.label),
            }
          : s
      )
    );
  };

  /* ── Render ────────────────────────────────────────────────────────────── */

  const s = activeSession;

  return (
    <main className="min-h-screen">
      <div className="mx-auto max-w-7xl px-4 pt-6 sm:px-6 lg:px-8">
        {/* ── Header ──────────────────────────────────────────────────── */}
        <div
          className="mb-6 flex items-center gap-4 rounded-2xl px-6 py-5"
          style={{
            background: "linear-gradient(90deg, #1a7a3c, #25a85a)",
            boxShadow: "0 4px 24px rgba(26,122,60,0.35)",
          }}
        >
          <span className="text-4xl">🎬</span>
          <div className="flex-1">
            <h1 className="text-2xl font-bold text-white">
              SuperLiving — Ad Generator
            </h1>
            <p className="mt-0.5 text-sm text-white/80">
              Transform your scripts into high-impact video ads for Tier 3 &amp;
              4 India · Powered by AI
            </p>
          </div>
        </div>

        {/* ── Worker Tabs ─────────────────────────────────────────────── */}
        <div className="mb-6 flex flex-wrap items-center gap-2">
          {sessions.map((sess) => {
            const isActive = sess.id === activeSessionId;
            const isRunning = sess.loading;
            const phaseEmoji =
              sess.phase === "result"
                ? "✅"
                : sess.phase === "review"
                ? "✏️"
                : "⚙️";
            return (
              <div key={sess.id} className="flex items-center">
                <button
                  onClick={() => setActiveSessionId(sess.id)}
                  className="flex items-center gap-2 rounded-l-xl px-4 py-2 text-sm font-semibold transition-all"
                  style={{
                    background: isActive
                      ? "linear-gradient(90deg, #1a7a3c, #25a85a)"
                      : "rgba(255,255,255,0.06)",
                    color: isActive ? "#fff" : "rgba(255,255,255,0.55)",
                    border: isActive
                      ? "1px solid rgba(37,168,90,0.6)"
                      : "1px solid rgba(255,255,255,0.08)",
                  }}
                >
                  {isRunning ? (
                    <svg
                      className="h-3.5 w-3.5 animate-spin"
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
                  ) : (
                    <span className="text-xs">{phaseEmoji}</span>
                  )}
                  {sess.label}
                  {isRunning && (
                    <span className="ml-1 text-xs opacity-70">Running…</span>
                  )}
                </button>
                {sessions.length > 1 && (
                  <button
                    onClick={() => removeWorker(sess.id)}
                    className="rounded-r-xl border-l border-white/10 px-2 py-2 text-xs text-white/40 transition hover:bg-red-500/20 hover:text-red-400"
                    style={{
                      background: isActive
                        ? "rgba(37,168,90,0.25)"
                        : "rgba(255,255,255,0.04)",
                      border: isActive
                        ? "1px solid rgba(37,168,90,0.4)"
                        : "1px solid rgba(255,255,255,0.08)",
                      borderLeft: "none",
                    }}
                    title="Remove worker"
                  >
                    ✕
                  </button>
                )}
              </div>
            );
          })}

          <button
            onClick={addWorker}
            className="flex items-center gap-1.5 rounded-xl border border-dashed border-[#25a85a]/40 px-4 py-2 text-sm text-[#7ecfa0] transition hover:border-[#25a85a]/70 hover:bg-[#25a85a]/10"
          >
            <span className="text-base leading-none">+</span>
            New Worker
          </button>

          <span className="ml-auto text-xs text-white/30">
            {sessions.filter((x) => x.loading).length > 0
              ? `${sessions.filter((x) => x.loading).length} worker(s) running in background`
              : "All workers idle"}
          </span>
        </div>

        {/* ── Error Banner ──────────────────────────────────────────────── */}
        <AnimatePresence>
          {s.error && (
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="mb-6 rounded-xl border border-red-500/30 bg-red-500/10 px-5 py-3 text-red-300"
            >
              ⚠️ {s.error}
              <button
                onClick={() => updateSession(s.id, { error: null })}
                className="ml-3 text-red-400 hover:text-red-200"
              >
                ✕
              </button>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Phase Router ──────────────────────────────────────────────── */}
        <AnimatePresence mode="wait">
          {s.phase === "input" && (
            <motion.div
              key={`input-${s.id}`}
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 20 }}
              transition={{ duration: 0.3 }}
            >
              <div className="grid gap-8 lg:grid-cols-5">
                <div className="lg:col-span-3">
                  <ConfigPanel
                    script={s.script}
                    setScript={(v) =>
                      updateSession(s.id, {
                        script:
                          typeof v === "function" ? v(s.script) : v,
                      })
                    }
                    extraPrompt={s.extraPrompt}
                    setExtraPrompt={(v) =>
                      updateSession(s.id, {
                        extraPrompt:
                          typeof v === "function" ? v(s.extraPrompt) : v,
                      })
                    }
                    numClips={s.numClips}
                    setNumClips={(v) =>
                      updateSession(s.id, {
                        numClips: typeof v === "function" ? v(s.numClips) : v,
                      })
                    }
                    durationLabel={s.durationLabel}
                    setDurationLabel={(v) =>
                      updateSession(s.id, {
                        durationLabel:
                          typeof v === "function" ? v(s.durationLabel) : v,
                      })
                    }
                    aspectRatio={s.aspectRatio}
                    setAspectRatio={(v) =>
                      updateSession(s.id, {
                        aspectRatio:
                          typeof v === "function" ? v(s.aspectRatio) : v,
                      })
                    }
                    veoModel={s.veoModel}
                    setVeoModel={(v) =>
                      updateSession(s.id, {
                        veoModel:
                          typeof v === "function" ? v(s.veoModel) : v,
                      })
                    }
                    languageNote={s.languageNote}
                    setLanguageNote={(v) =>
                      updateSession(s.id, {
                        languageNote:
                          typeof v === "function" ? v(s.languageNote) : v,
                      })
                    }
                  />
                </div>
                <div className="lg:col-span-2">
                  <CharacterUpload
                    usePhotos={s.usePhotos}
                    setUsePhotos={(v) =>
                      updateSession(s.id, {
                        usePhotos:
                          typeof v === "function" ? v(s.usePhotos) : v,
                      })
                    }
                    characters={s.characters}
                    setCharacters={(v) =>
                      updateSession(s.id, {
                        characters:
                          typeof v === "function" ? v(s.characters) : v,
                      })
                    }
                  />
                </div>
              </div>

              <div className="mt-8 flex justify-center">
                <button
                  onClick={() => handleGeneratePrompts(s.id)}
                  disabled={s.loading}
                  className="rounded-xl px-10 py-3.5 text-lg font-bold text-white transition-all hover:opacity-90 hover:-translate-y-0.5 disabled:opacity-50 disabled:cursor-not-allowed"
                  style={{
                    background: "linear-gradient(90deg, #1a7a3c, #25a85a)",
                  }}
                >
                  {s.loading ? (
                    <span className="flex items-center gap-2">
                      <svg className="h-5 w-5 animate-spin" viewBox="0 0 24 24">
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

          {s.phase === "review" && (
            <motion.div
              key={`review-${s.id}`}
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 20 }}
              transition={{ duration: 0.3 }}
            >
              <PromptEditor
                clips={s.clips}
                setClips={(v) =>
                  updateSession(s.id, {
                    clips: typeof v === "function" ? v(s.clips) : v,
                  })
                }
                characterSheet={s.characterSheet}
                setCharacterSheet={(v) =>
                  updateSession(s.id, {
                    characterSheet:
                      typeof v === "function" ? v(s.characterSheet) : v,
                  })
                }
                onConfirm={() => handleGenerateVideo(s.id)}
                onBack={() => updateSession(s.id, { phase: "input" })}
                loading={s.loading}
              />
            </motion.div>
          )}

          {s.phase === "result" && (
            <motion.div
              key={`result-${s.id}`}
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 20 }}
              transition={{ duration: 0.3 }}
            >
              <VideoResult
                videoUrl={s.videoUrl}
                clips={s.clips}
                setClips={(v) =>
                  updateSession(s.id, {
                    clips: typeof v === "function" ? v(s.clips) : v,
                  })
                }
                numClips={s.numClips}
                onRegenerate={(indices) => handleRegenerate(s.id, indices)}
                onReset={() => handleReset(s.id)}
                loading={s.loading}
              />
            </motion.div>
          )}
        </AnimatePresence>

        <p className="mt-12 pb-8 text-center text-xs text-[#555]">
          SuperLiving Internal Tool · AI-Powered Ad Generator · 8s max per clip
        </p>
      </div>
    </main>
  );
}
"use client";

import { Dispatch, SetStateAction } from "react";

interface Props {
  script: string;              setScript: Dispatch<SetStateAction<string>>;
  extraPrompt: string;         setExtraPrompt: Dispatch<SetStateAction<string>>;
  numClips: number;            setNumClips: Dispatch<SetStateAction<number>>;
  durationLabel: string;       setDurationLabel: Dispatch<SetStateAction<string>>;
  aspectRatio: string;         setAspectRatio: Dispatch<SetStateAction<string>>;
  veoModel: string;            setVeoModel: Dispatch<SetStateAction<string>>;
  languageNote: boolean;       setLanguageNote: Dispatch<SetStateAction<boolean>>;
}

const DURATION_MAP: Record<string, number> = {
  "15s": 2, "22s": 3, "30s": 4, "37s": 5, "45s": 6, "52s": 7, "60s": 8,
};

const ASPECT_OPTIONS = [
  "9:16 (Reels / Shorts)",
  "16:9 (YouTube / Landscape)",
];

const VEO_MODELS = [
  { label: "Veo 3.1 Preview", value: "veo-3.1-generate-preview" },
  { label: "Veo 3.0 Preview", value: "veo-3.0-generate-preview" },
];

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="mb-2 text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>
      {children}
    </p>
  );
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <label className="mb-1.5 block text-xs" style={{ color: "var(--text-secondary)" }}>
      {children}
    </label>
  );
}

export default function ConfigPanel({
  script, setScript,
  extraPrompt, setExtraPrompt,
  setNumClips, durationLabel, setDurationLabel,
  aspectRatio, setAspectRatio,
  veoModel, setVeoModel,
  languageNote, setLanguageNote,
}: Props) {
  const handleDurationChange = (dur: string) => {
    setDurationLabel(dur);
    setNumClips(DURATION_MAP[dur] ?? 6);
  };

  const wordCount = script.split(/\s+/).filter(Boolean).length;

  return (
    <div className="space-y-5">

      {/* ── Script ──────────────────────────────────────────────── */}
      <div className="glass rounded-2xl p-5 fade-up" style={{ animationDelay: "0.04s" }}>
        <SectionLabel>📝 Ad Script</SectionLabel>
        <textarea
          value={script}
          onChange={(e) => setScript(e.target.value)}
          rows={11}
          placeholder="Paste your ad script here (Hindi / English)…"
          className="field w-full resize-y rounded-xl px-4 py-3 text-sm leading-relaxed"
          style={{ minHeight: 220 }}
        />
        <div className="mt-2 flex items-center justify-between">
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            Hindi &amp; English supported
          </span>
          <span
            className="rounded-lg px-2 py-0.5 text-xs font-mono"
            style={{
              background: "rgba(255,255,255,0.04)",
              color: wordCount > 0 ? "var(--text-secondary)" : "var(--text-muted)",
            }}
          >
            {wordCount} words
          </span>
        </div>
      </div>

      {/* ── Settings ────────────────────────────────────────────── */}
      <div className="glass rounded-2xl p-5 fade-up" style={{ animationDelay: "0.08s" }}>
        <SectionLabel>⚙️ Settings</SectionLabel>
        <div className="grid gap-4 sm:grid-cols-2">

          <div>
            <FieldLabel>Duration</FieldLabel>
            <select
              value={durationLabel}
              onChange={(e) => handleDurationChange(e.target.value)}
              className="field w-full rounded-lg px-3 py-2.5 text-sm"
            >
              {Object.keys(DURATION_MAP).map((d) => (
                <option key={d} value={d}>
                  {d} ({DURATION_MAP[d]} clips)
                </option>
              ))}
            </select>
          </div>

          <div>
            <FieldLabel>Aspect Ratio</FieldLabel>
            <select
              value={aspectRatio}
              onChange={(e) => setAspectRatio(e.target.value)}
              className="field w-full rounded-lg px-3 py-2.5 text-sm"
            >
              {ASPECT_OPTIONS.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
          </div>

          <div>
            <FieldLabel>Veo Model</FieldLabel>
            <select
              value={veoModel}
              onChange={(e) => setVeoModel(e.target.value)}
              className="field w-full rounded-lg px-3 py-2.5 text-sm"
            >
              {VEO_MODELS.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </div>

          {/* Toggle */}
          <div className="flex items-center gap-3 pt-3">
            <label className="flex cursor-pointer items-center gap-2.5 select-none">
              <div
                className="relative h-5 w-9 rounded-full transition-colors duration-200"
                style={{ background: languageNote ? "var(--accent)" : "rgba(255,255,255,0.1)" }}
                onClick={() => setLanguageNote((v) => !v)}
              >
                <div
                  className="absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform duration-200"
                  style={{ left: languageNote ? "calc(100% - 18px)" : "2px" }}
                />
              </div>
              <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
                Dialogue tone note
              </span>
            </label>
          </div>
        </div>
      </div>

      {/* ── Additional instructions ──────────────────────────────── */}
      <div className="glass rounded-2xl p-5 fade-up" style={{ animationDelay: "0.12s" }}>
        <SectionLabel>📎 Additional Instructions</SectionLabel>
        <textarea
          value={extraPrompt}
          onChange={(e) => setExtraPrompt(e.target.value)}
          rows={3}
          placeholder="Brand guidelines, visual references, style notes…"
          className="field w-full resize-y rounded-xl px-4 py-3 text-sm"
        />
      </div>
    </div>
  );
}
"use client";

import { Dispatch, SetStateAction, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";

interface CharacterSlot {
  name: string;
  file: File | null;
}

interface Props {
  usePhotos: boolean;
  setUsePhotos: Dispatch<SetStateAction<boolean>>;
  characters: CharacterSlot[];
  setCharacters: Dispatch<SetStateAction<CharacterSlot[]>>;
}

export default function CharacterUpload({
  usePhotos, setUsePhotos, characters, setCharacters,
}: Props) {
  const fileRefs = useRef<(HTMLInputElement | null)[]>([]);

  const updateCharacter = useCallback(
    (index: number, field: keyof CharacterSlot, value: string | File | null) => {
      setCharacters((prev) => {
        const copy = [...prev];
        copy[index] = { ...copy[index], [field]: value };
        return copy;
      });
    },
    [setCharacters]
  );

  const addSlot = () =>
    setCharacters((prev) => [...prev, { name: "", file: null }]);

  const removeSlot = (index: number) =>
    setCharacters((prev) => prev.filter((_, i) => i !== index));

  const handleDrop = (index: number, e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer.files?.[0];
    if (file?.type.startsWith("image/")) updateCharacter(index, "file", file);
  };

  return (
    <div className="glass rounded-2xl p-5 fade-up" style={{ animationDelay: "0.16s" }}>
      {/* Header row */}
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>
            📸 Character Photos
          </p>
          <p className="mt-0.5 text-xs" style={{ color: "var(--text-muted)" }}>
            Optional — enables face-locked I2V generation
          </p>
        </div>

        {/* Toggle switch */}
        <button
          role="switch"
          aria-checked={usePhotos}
          onClick={() => setUsePhotos((v) => !v)}
          className="relative shrink-0 h-6 w-11 rounded-full transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          style={{ background: usePhotos ? "var(--accent)" : "rgba(255,255,255,0.12)" }}
        >
          <span
            className="absolute top-1 h-4 w-4 rounded-full bg-white shadow-md transition-transform duration-200"
            style={{ left: usePhotos ? "calc(100% - 20px)" : "4px" }}
          />
          <span className="sr-only">{usePhotos ? "Disable" : "Enable"} character photos</span>
        </button>
      </div>

      {/* Content */}
      <AnimatePresence mode="wait">
        {!usePhotos ? (
          <motion.div
            key="off"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="flex flex-col items-center justify-center gap-2 rounded-xl py-8 text-center"
            style={{
              background: "rgba(255,255,255,0.02)",
              border: "1px dashed var(--border-subtle)",
            }}
          >
            <span className="text-2xl opacity-25">📁</span>
            <p className="max-w-[35] text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
              Enable to upload reference photos for face-locked generation. Without photos, Gemini auto-generates a character sheet.
            </p>
          </motion.div>
        ) : (
          <motion.div
            key="on"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            transition={{ duration: 0.25 }}
            className="space-y-3"
          >
            {characters.map((char, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.05 }}
                className="rounded-xl p-3"
                style={{
                  background: "rgba(255,255,255,0.025)",
                  border: "1px solid var(--border-subtle)",
                }}
              >
                {/* Name row */}
                <div className="mb-2.5 flex items-center gap-2">
                  <input
                    type="text"
                    value={char.name}
                    onChange={(e) => updateCharacter(i, "name", e.target.value)}
                    placeholder={`Character ${i + 1} name`}
                    className="field flex-1 rounded-lg px-3 py-1.5 text-sm"
                  />
                  {characters.length > 1 && (
                    <button
                      onClick={() => removeSlot(i)}
                      className="shrink-0 text-xs transition-colors hover:opacity-100"
                      style={{ color: "var(--error)", opacity: 0.6 }}
                      title="Remove character"
                    >
                      ✕
                    </button>
                  )}
                </div>

                {/* Drop zone */}
                <div
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => handleDrop(i, e)}
                  onClick={() => fileRefs.current[i]?.click()}
                  className="flex cursor-pointer flex-col items-center justify-center rounded-lg p-4 text-center transition-all duration-200"
                  style={{
                    border: `2px dashed ${char.file ? "var(--accent)" : "var(--border-subtle)"}`,
                    background: char.file ? "rgba(99,102,241,0.06)" : "transparent",
                  }}
                >
                  <input
                    ref={(el) => { fileRefs.current[i] = el; }}
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    className="hidden"
                    onChange={(e) =>
                      updateCharacter(i, "file", e.target.files?.[0] ?? null)
                    }
                  />

                  {char.file ? (
                    <div className="flex items-center gap-2 text-xs">
                      <span style={{ color: "var(--success)" }}>✓</span>
                      <span className="truncate max-w-[35]" style={{ color: "var(--text-secondary)" }}>
                        {char.file.name}
                      </span>
                      <span style={{ color: "var(--text-muted)" }}>
                        ({(char.file.size / 1024).toFixed(0)} KB)
                      </span>
                    </div>
                  ) : (
                    <div>
                      <span className="block text-xl opacity-25 mb-1">⬆</span>
                      <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                        Drop image or click to upload
                      </p>
                      <p className="text-xs mt-0.5" style={{ color: "var(--text-muted)", opacity: 0.6 }}>
                        JPG, PNG, WebP · max 10 MB
                      </p>
                    </div>
                  )}
                </div>
              </motion.div>
            ))}

            {/* Add character button */}
            <button
              onClick={addSlot}
              className="w-full rounded-xl py-2 text-xs font-medium transition-all duration-200"
              style={{
                border: "1px dashed var(--border-subtle)",
                color: "var(--accent-bright)",
                background: "transparent",
              }}
            >
              + Add Character
            </button>

            {/* Continuity note */}
            <p className="text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
              🔒 <strong style={{ color: "var(--text-secondary)" }}>Continuity:</strong> Clip 1 uses the photo as I2V frame 0. Clips 2+ use the exact last frame of the previous clip for seamless match-cuts.
            </p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
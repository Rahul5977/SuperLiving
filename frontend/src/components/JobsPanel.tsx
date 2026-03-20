"use client";

import { motion, AnimatePresence } from "framer-motion";
import type { Job } from "@/hooks/useJobPoller";

interface Props {
  jobs: Job[];
  activeJobId: string | null;
  onOpenJob: (job: Job) => void;
}

const STATUS_META: Record<
  Job["status"],
  { label: string; dotColor: string; textColor: string; pulse: boolean }
> = {
  pending:    { label: "Queued",     dotColor: "#64748b", textColor: "var(--text-muted)",     pulse: false },
  generating: { label: "Generating", dotColor: "var(--accent)",  textColor: "var(--accent-bright)", pulse: true  },
  stitching:  { label: "Stitching",  dotColor: "var(--accent-2)", textColor: "var(--accent-2)",     pulse: true  },
  done:       { label: "Done ✓",    dotColor: "var(--success)", textColor: "var(--success)",      pulse: false },
  error:      { label: "Failed",    dotColor: "var(--error)",   textColor: "var(--error)",        pulse: false },
};

const WORKER_COUNT = 4;

export default function JobsPanel({ jobs, activeJobId, onOpenJob }: Props) {
  const runningJobs = jobs.filter((j) =>
    ["pending", "generating", "stitching"].includes(j.status)
  );
  const completedJobs = jobs.filter(
    (j) => j.status === "done" || j.status === "error"
  );

  return (
    <div className="space-y-4">
      {/* Panel header */}
      <div className="flex items-center justify-between px-0.5">
        <h3 className="text-sm font-semibold" style={{ color: "var(--text-secondary)" }}>
          Generation Jobs
        </h3>
        {jobs.length > 0 && (
          <span
            className="rounded-full px-2.5 py-0.5 text-xs font-medium"
            style={{ background: "rgba(99,102,241,0.15)", color: "var(--accent-bright)" }}
          >
            {runningJobs.length}/{WORKER_COUNT} workers
          </span>
        )}
      </div>

      {/* Worker slots */}
      <div className="grid grid-cols-4 gap-1.5">
        {Array.from({ length: WORKER_COUNT }, (_, slotIdx) => {
          const job = runningJobs[slotIdx];
          const busy = !!job;
          return (
            <div
              key={slotIdx}
              className="rounded-xl py-2.5 text-center transition-all duration-300"
              style={{
                background: busy ? "rgba(99,102,241,0.10)" : "rgba(255,255,255,0.025)",
                border: `1px solid ${busy ? "var(--border-bright)" : "var(--border-subtle)"}`,
                boxShadow: busy ? "0 0 14px var(--accent-glow)" : "none",
              }}
            >
              <p
                className="text-sm font-mono font-bold"
                style={{ color: busy ? "var(--accent-bright)" : "var(--text-muted)" }}
              >
                #{slotIdx + 1}
              </p>
              <p
                className="mt-0.5 text-xs"
                style={{ color: busy ? "var(--accent-2)" : "var(--text-muted)" }}
              >
                {busy ? "busy" : "idle"}
              </p>
            </div>
          );
        })}
      </div>

      {/* Empty state */}
      {jobs.length === 0 && (
        <div
          className="flex flex-col items-center justify-center gap-3 rounded-2xl py-10 text-center"
          style={{ background: "rgba(255,255,255,0.02)", border: "1px dashed var(--border-subtle)" }}
        >
          <span className="text-3xl opacity-20">⚡</span>
          <div>
            <p className="text-sm font-medium" style={{ color: "var(--text-secondary)" }}>
              Parallel Workers Ready
            </p>
            <p className="mt-1 max-w-50 text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
              Generate prompts and start generating to run up to 4 jobs simultaneously.
            </p>
          </div>
        </div>
      )}

      {/* Active jobs */}
      <AnimatePresence>
        {runningJobs.map((job) => (
          <JobCard
            key={job.id}
            job={job}
            isActive={job.id === activeJobId}
            onOpen={onOpenJob}
          />
        ))}
      </AnimatePresence>

      {/* Completed jobs */}
      {completedJobs.length > 0 && (
        <div className="space-y-2">
          <p className="px-0.5 text-xs" style={{ color: "var(--text-muted)" }}>
            Completed ({completedJobs.length})
          </p>
          <AnimatePresence>
            {completedJobs.slice(0, 6).map((job) => (
              <JobCard
                key={job.id}
                job={job}
                isActive={job.id === activeJobId}
                onOpen={onOpenJob}
              />
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}

// ── Individual job card ────────────────────────────────────────────────────

function JobCard({
  job,
  isActive,
  onOpen,
}: {
  job: Job;
  isActive: boolean;
  onOpen: (j: Job) => void;
}) {
  const meta = STATUS_META[job.status];
  const isRunning = ["pending", "generating", "stitching"].includes(job.status);
  const isClickable = job.status === "done" && !!job.result;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -8, scale: 0.96 }}
      transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] as [number, number, number, number] }}
      onClick={() => isClickable && onOpen(job)}
      className="glass rounded-xl p-3 transition-all duration-200"
      style={{
        cursor: isClickable ? "pointer" : "default",
        borderColor: isActive ? "var(--border-bright)" : undefined,
        boxShadow: isActive ? "0 0 18px var(--accent-glow)" : undefined,
      }}
    >
      <div className="flex items-start gap-2.5">
        {/* Status dot */}
        <div className="mt-1 shrink-0">
          <span
            className="block h-2 w-2 rounded-full"
            style={{
              background: meta.dotColor,
              boxShadow: isRunning ? `0 0 6px ${meta.dotColor}` : "none",
              animation: meta.pulse ? "pulse-dot 1.5s ease-in-out infinite" : "none",
            }}
          />
        </div>

        {/* Content */}
        <div className="min-w-0 flex-1">
          <p
            className="truncate text-xs font-medium"
            style={{ color: "var(--text-primary)" }}
          >
            {job.label}
          </p>

          <p
            className="mt-0.5 truncate text-xs"
            style={{ color: meta.textColor }}
          >
            {job.step || meta.label}
          </p>

          {/* Progress bar */}
          {isRunning && (
            <div
              className="mt-2 h-1 w-full overflow-hidden rounded-full"
              style={{ background: "rgba(255,255,255,0.06)" }}
            >
              <div
                className="progress-bar-fill h-full rounded-full"
                style={{ width: `${job.progress}%` }}
              />
            </div>
          )}

          {/* CTA for completed */}
          {job.status === "done" && (
            <p className="mt-1.5 text-xs font-medium" style={{ color: "var(--accent-bright)" }}>
              Click to view →
            </p>
          )}

          {/* Error message */}
          {job.status === "error" && job.error && (
            <p className="mt-1 truncate text-xs" style={{ color: "var(--error)" }}>
              {job.error.slice(0, 55)}…
            </p>
          )}
        </div>

        {/* Progress % badge */}
        {isRunning && (
          <span
            className="shrink-0 text-xs font-mono"
            style={{ color: "var(--text-muted)" }}
          >
            {job.progress}%
          </span>
        )}
      </div>
    </motion.div>
  );
}
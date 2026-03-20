"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type JobStatus = "pending" | "generating" | "stitching" | "done" | "error";

export interface Job {
  id: string;
  label: string;
  status: JobStatus;
  step: string;
  progress: number;
  result?: { video_url: string; clip_paths: string[]; message: string } | null;
  error?: string | null;
  createdAt: number;
}



interface UseJobPollerOptions {
  apiBase: string;
  /** Called when a job transitions to "done" */
  onJobDone?: (job: Job) => void;
  /** Called when a job transitions to "error" */
  onJobError?: (job: Job) => void;
  /** Poll interval in ms. Default 3000. */
  pollIntervalMs?: number;
}
function mapStatus(backendStatus: string): JobStatus {
  switch (backendStatus) {
    case "pending":   return "pending";
    case "running":   return "generating";
    case "done":      return "done";
    case "failed":
    case "cancelled": return "error";
    default:          return "pending";
  }
}

export function useJobPoller({
  apiBase,
  onJobDone,
  onJobError,
  pollIntervalMs = 3000,
}: UseJobPollerOptions) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const pollersRef = useRef<Record<string, ReturnType<typeof setInterval>>>({});
  const onJobDoneRef = useRef(onJobDone);
  const onJobErrorRef = useRef(onJobError);

  // Keep refs up to date without re-registering pollers
  useEffect(() => { onJobDoneRef.current = onJobDone; }, [onJobDone]);
  useEffect(() => { onJobErrorRef.current = onJobError; }, [onJobError]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      Object.values(pollersRef.current).forEach(clearInterval);
    };
  }, []);

  const stopPolling = useCallback((jobId: string) => {
    if (pollersRef.current[jobId]) {
      clearInterval(pollersRef.current[jobId]);
      delete pollersRef.current[jobId];
    }
  }, []);

  const startPolling = useCallback(
    (jobId: string) => {
      if (pollersRef.current[jobId]) return; // already polling

      const poll = async () => {
        try {
          const res = await fetch(`${apiBase}/api/job-status/${jobId}`);
          if (!res.ok) return;
          const data = await res.json();
          const mappedStatus = mapStatus(data.status);
          setJobs((prev) =>
            prev.map((j) =>
              j.id === jobId
                ? {
                    ...j,
                    status: mappedStatus,
                    step: data.step ?? j.step,
                    progress: data.progress ?? j.progress,
                    result: data.result ?? j.result,
                    error: data.error ?? j.error,
                  }
                : j
            )
          );

          if (data.status === "done") {
            stopPolling(jobId);
            // Fire callback with the updated job
            const updatedJob: Job = {
              id: jobId,
              label: "",
              status: "done",
              step: data.step ?? "Complete!",
              progress: 100,
              result: data.result,
              error: null,
              createdAt: 0,
            };
            onJobDoneRef.current?.(updatedJob);
          } else if (data.status === "error") {
            stopPolling(jobId);
            const updatedJob: Job = {
              id: jobId,
              label: "",
              status: "error",
              step: "Failed",
              progress: 0,
              result: null,
              error: data.error,
              createdAt: 0,
            };
            onJobErrorRef.current?.(updatedJob);
          }
        } catch (err) {
          console.warn(`[useJobPoller] poll error for ${jobId}:`, err);
        }
      };

      pollersRef.current[jobId] = setInterval(poll, pollIntervalMs);
      poll(); // immediate first check
    },
    [apiBase, pollIntervalMs, stopPolling]
  );

  /**
   * Enqueue a new job that has already been submitted to the backend.
   * Starts polling immediately.
   */
  const addJob = useCallback(
    (job: Job) => {
      setJobs((prev) => [job, ...prev]);
      setActiveJobId(job.id);
      startPolling(job.id);
    },
    [startPolling]
  );

  /**
   * Make a specific job the "active" one (shown in main content area).
   */
  const setActive = useCallback((jobId: string | null) => {
    setActiveJobId(jobId);
  }, []);

  const activeJob = jobs.find((j) => j.id === activeJobId) ?? null;

  return { jobs, activeJob, activeJobId, addJob, setActive };
}
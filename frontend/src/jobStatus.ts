import type { JobStatus, JobStatusFullResponse } from "./api/client";

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  parsing: "Parsing",
  parsed: "Parsed",
  extracting: "Extracting",
  done: "Done",
  failed: "Failed",
};

type WaitingJob = Extract<JobStatusFullResponse, { waiting_for: unknown }>;
export type WaitingFor = NonNullable<WaitingJob["waiting_for"]>;

// The service a deferred job waits for. The parse worker checks only MinerU, and the
// extract worker checks the vLLM server that runs the extraction model.
export const WAITING_FOR_NAMES: Record<WaitingFor, string> = {
  parser: "MinerU",
  extractor: "the extraction model",
};

// What the job is waiting for, or null when it is not waiting.
export function waitingFor(job: JobStatusFullResponse): { service: string; since: string } | null {
  if (job.status === "failed" || !job.waiting_for || !job.waiting_since) return null;
  return { service: WAITING_FOR_NAMES[job.waiting_for], since: job.waiting_since };
}

// The status shown in a job list: a waiting job names what it waits for.
export function jobStatusLabel(job: JobStatusFullResponse): string {
  const wait = waitingFor(job);
  return wait ? `Waiting for ${wait.service}` : JOB_STATUS_LABELS[job.status];
}

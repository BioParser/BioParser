import type { JobStatus } from "./api/client";

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  parsing: "Parsing",
  parsed: "Parsed",
  extracting: "Extracting",
  done: "Done",
  failed: "Failed",
};

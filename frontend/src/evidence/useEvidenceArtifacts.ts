import { useQuery } from "@tanstack/react-query";
import {
  ApiError,
  type ExtractionArtifact,
  getExtractionArtifact,
  getParserArtifact,
} from "../api/client";
import { shouldRetry } from "../api/queryClient";
import { indexTextBlocks, type Observation } from "./quotation";

// Not under ["jobs", ...]: invalidating the job list must not fetch artifacts of several MB again.
export function artifactQueryKey(kind: "parser" | "extraction", jobId: string) {
  return ["artifacts", kind, jobId] as const;
}

// A stored artifact the API cannot read (500 artifact_invalid) is read the same way next time.
export function retryArtifact(failureCount: number, error: Error): boolean {
  if (error instanceof ApiError && error.code === "artifact_invalid") {
    return false;
  }
  return shouldRetry(failureCount, error);
}

function observationsOf(artifact: ExtractionArtifact): Observation[] {
  return artifact.observations ?? [];
}

// Both artifacts of a job, fetched once and kept while the job is open: the API serves them
// as immutable. Nothing is fetched while `enabled` is false, so the view loads them only once
// an observation is selected. Each query fails on its own, so the view can keep what loaded.
export function useEvidenceArtifacts(jobId: string, enabled: boolean) {
  const blocks = useQuery({
    queryKey: artifactQueryKey("parser", jobId),
    queryFn: ({ signal }) => getParserArtifact(jobId, signal),
    select: indexTextBlocks,
    staleTime: Number.POSITIVE_INFINITY,
    retry: retryArtifact,
    enabled,
  });
  const observations = useQuery({
    queryKey: artifactQueryKey("extraction", jobId),
    queryFn: ({ signal }) => getExtractionArtifact(jobId, signal),
    select: observationsOf,
    staleTime: Number.POSITIVE_INFINITY,
    retry: retryArtifact,
    enabled,
  });
  return { blocks, observations };
}

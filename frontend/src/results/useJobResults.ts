import { useQuery } from "@tanstack/react-query";
import { getJobEts } from "../api/client";
import { retryArtifact } from "../evidence/useEvidenceArtifacts";

// Not under ["job", ...] or ["jobs", ...]: the job poll and the dashboard must not load the
// rows again. A done job's rows never change, because its extraction artifact never changes.
export function resultsQueryKey(jobId: string) {
  return ["results", jobId] as const;
}

export function useJobResults(jobId: string) {
  return useQuery({
    queryKey: resultsQueryKey(jobId),
    queryFn: ({ signal }) => getJobEts(jobId, signal),
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    // 503 and network errors are tried again; artifact_invalid and other 4xx are final.
    retry: retryArtifact,
  });
}

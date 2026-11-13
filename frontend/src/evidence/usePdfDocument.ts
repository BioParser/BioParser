import { skipToken, useQuery } from "@tanstack/react-query";
import { ApiError, getDocumentFile, getJob, type JobStatusResponse } from "../api/client";
import { retryArtifact } from "./useEvidenceArtifacts";

// The same key as the job page, so a job it has loaded is not fetched again.
export function jobQueryKey(jobId: string) {
  return ["job", jobId] as const;
}

export function pdfQueryKey(checksum: string) {
  return ["documents", checksum, "pdf"] as const;
}

function documentChecksumOf(job: JobStatusResponse): string {
  return job.document_checksum;
}

// pdf.js refusing the bytes (not a PDF, damaged) happens again on every try. A failed request
// follows the artifact rule: retried unless the API cannot read the stored file.
export function retryDocument(failureCount: number, error: Error): boolean {
  if (!(error instanceof ApiError) && !(error instanceof TypeError)) {
    return false;
  }
  return retryArtifact(failureCount, error);
}

// The job's PDF, opened with pdf.js. It is fetched and opened once per document and reused
// for every observation; nothing loads while `enabled` is false.
export function usePdfDocument(jobId: string, enabled: boolean) {
  const checksum = useQuery({
    queryKey: jobQueryKey(jobId),
    queryFn: ({ signal }) => getJob(jobId, signal),
    select: documentChecksumOf,
    // The checksum never changes; the job page polls the status on its own.
    staleTime: Number.POSITIVE_INFINITY,
    enabled,
  });
  const documentChecksum = checksum.data;
  const pdf = useQuery({
    queryKey: pdfQueryKey(documentChecksum ?? ""),
    queryFn:
      documentChecksum === undefined
        ? skipToken
        : async ({ signal }) => {
            const bytes = await getDocumentFile(documentChecksum, signal);
            const { openPdf } = await import("./pdfjs");
            return openPdf(bytes);
          },
    staleTime: Number.POSITIVE_INFINITY,
    // A PDFDocumentProxy is a live object, not data to compare.
    structuralSharing: false,
    retry: retryDocument,
  });
  return { checksum, pdf };
}

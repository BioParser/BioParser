import { type QueryClient, type QueryKey, skipToken, useQuery } from "@tanstack/react-query";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { ApiError, getDocumentFile, getJob, type JobStatusResponse } from "../api/client";
import { retryArtifact } from "./useEvidenceArtifacts";

// The same key as the job page, so a job it has loaded is not fetched again.
export function jobQueryKey(jobId: string) {
  return ["job", jobId] as const;
}

export function pdfQueryKey(checksum: string) {
  return ["documents", checksum, "pdf"] as const;
}

function isPdfQueryKey(key: QueryKey): boolean {
  return key.length === 3 && key[0] === "documents" && key[2] === "pdf";
}

// pdf.js starts a worker for each opened document, and only destroying its loading task ends
// it. React Query removes a PDF once no observation has shown it for gcTime, or at once on
// clear() when the API key is forgotten; its worker is ended with it.
export function destroyPdfsOnRemove(queryClient: QueryClient): () => void {
  return queryClient.getQueryCache().subscribe((event) => {
    if (event.type !== "removed" || !isPdfQueryKey(event.query.queryKey)) {
      return;
    }
    const pdf = event.query.state.data as PDFDocumentProxy | undefined;
    void pdf?.loadingTask.destroy();
  });
}

function documentChecksumOf(job: JobStatusResponse): string {
  if (!("document_checksum" in job)) {
    throw new Error("The job has no document checksum.");
  }
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
    queryFn: ({ signal }) => getJob(jobId, signal, "full"),
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
            const document = await openPdf(bytes);
            // A query cancelled while pdf.js was opening drops the result, so the
            // removal above never sees this document: end its worker here.
            if (signal.aborted) {
              void document.loadingTask.destroy();
              signal.throwIfAborted();
            }
            return document;
          },
    staleTime: Number.POSITIVE_INFINITY,
    // A PDFDocumentProxy is a live object, not data to compare.
    structuralSharing: false,
    retry: retryDocument,
  });
  return { checksum, pdf };
}

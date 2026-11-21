import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { ApiError, getJob, type JobStatusFullResponse } from "../api/client";
import { EvidenceView } from "../evidence/EvidenceView";
import { JOB_STATUS_LABELS } from "../jobStatus";
import { JobResults } from "../results/JobResults";

const OBSERVATION_PARAM = "observation";

const STAGES = ["queued", "parsing", "parsed", "extracting", "done"] as const;
type FailureCode = Extract<JobStatusFullResponse, { status: "failed" }>["error"]["code"];
const FAILURE_MESSAGES: Record<FailureCode, string> = {
  invalid_pdf: "The file could not be read as a valid PDF.",
  parse_failed: "The PDF could not be parsed.",
  extraction_failed: "Traits could not be extracted from the parsed document.",
  timeout: "The job took too long to complete.",
  internal_error: "An unexpected error stopped the job.",
};

function isPermanentError(error: Error | null): boolean {
  return (
    error instanceof ApiError &&
    error.status >= 400 &&
    error.status < 500 &&
    error.status !== 408 &&
    error.status !== 429
  );
}

export function JobDetailPage() {
  const { jobId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const observationIndex = parseObservationIndex(searchParams.get(OBSERVATION_PARAM));
  // Each selection is a history entry: Back returns to the previous observation, then closes.
  function openEvidence(index: number) {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set(OBSERVATION_PARAM, String(index));
      return next;
    });
  }
  function closeEvidence() {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      next.delete(OBSERVATION_PARAM);
      return next;
    });
  }
  const showEvidence = observationIndex !== null;
  const [visible, setVisible] = useState(document.visibilityState !== "hidden");
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);

  const job = useQuery({
    // detail=full: the page shows the stored record, and usePdfDocument reads the document
    // checksum from the same cache entry.
    queryKey: ["job", jobId],
    queryFn: ({ signal }) => getJob(jobId, signal, "full"),
    enabled: visible,
    // Polling is also the retry schedule, so transient failures remain visible.
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: (query) => {
      if (!visible || isPermanentError(query.state.error)) return false;
      const status = query.state.data?.status;
      return status === "done" || status === "failed" ? false : 3000;
    },
  });
  const missing = job.error instanceof ApiError && [404, 422].includes(job.error.status);

  return (
    <div className={showEvidence ? "job-detail job-detail-with-evidence" : "job-detail"}>
      <div>
        <h1>Job</h1>
        <p>
          Job ID: <code>{jobId}</code>
        </p>
        {job.isPending && <p role="status">Loading job…</p>}
        {job.error && (
          <p role="alert">
            {missing
              ? "There is no such job."
              : isPermanentError(job.error)
                ? "The job could not be loaded. Check your access and try again."
                : "The API is temporarily unavailable. Retrying automatically…"}
          </p>
        )}
        {!missing && job.data && (
          <JobStatus
            job={job.data}
            observationIndex={observationIndex}
            onSelectObservation={openEvidence}
          />
        )}
      </div>
      {showEvidence && (
        <EvidenceView jobId={jobId} observationIndex={observationIndex} onClose={closeEvidence} />
      )}
    </div>
  );
}

function JobStatus({
  job,
  observationIndex,
  onSelectObservation,
}: {
  job: JobStatusFullResponse;
  observationIndex: number | null;
  onSelectObservation: (index: number) => void;
}) {
  const stage =
    job.status === "failed"
      ? STAGES.indexOf(job.has_parse_result ? "extracting" : "parsing")
      : STAGES.indexOf(job.status);
  return (
    <>
      <section className="job-summary" aria-labelledby="job-details-heading">
        <h2 id="job-details-heading">{job.display_name ?? "Unnamed PDF"}</h2>
        <dl>
          <dt>Parser</dt>
          <dd>{job.parser}</dd>
          <dt>Extractor</dt>
          <dd>{job.extractor}</dd>
          <dt>Created</dt>
          <dd>
            <time dateTime={job.created_at}>{new Date(job.created_at).toLocaleString()}</time>
          </dd>
        </dl>
      </section>
      <section aria-labelledby="job-progress-heading">
        <h2 id="job-progress-heading">Progress</h2>
        <p role="status">Status: {job.status}</p>
        <ol className="job-detail-progress" aria-label="Job progress">
          {STAGES.map((status, index) => (
            <li key={status} aria-current={index === stage ? "step" : undefined}>
              {JOB_STATUS_LABELS[status]}
              {index === stage && job.status === "failed" ? " — failed" : ""}
              {index < stage ? " — complete" : ""}
            </li>
          ))}
        </ol>
      </section>
      {job.status === "failed" && (
        <div role="alert" className="job-failure">
          <p>
            Job failed: <code>{job.error.code}</code>
          </p>
          <p>{FAILURE_MESSAGES[job.error.code]}</p>
          {job.has_parse_result && <p>The parse result is kept.</p>}
        </div>
      )}
      {job.status === "done" && (
        <section aria-labelledby="job-results-heading">
          <h2 id="job-results-heading">Results</h2>
          <JobResults
            jobId={job.job_id}
            displayName={job.display_name}
            selectedIndex={observationIndex}
            onSelect={onSelectObservation}
          />
        </section>
      )}
    </>
  );
}

function parseObservationIndex(value: string | null): number | null {
  return value !== null && /^\d+$/.test(value) ? Number(value) : null;
}

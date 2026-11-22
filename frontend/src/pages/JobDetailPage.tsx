import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
import {
  ApiError,
  deleteJob,
  getJob,
  type JobList,
  type JobStatusFullResponse,
} from "../api/client";
import { EvidenceView } from "../evidence/EvidenceView";
import { artifactQueryKey } from "../evidence/useEvidenceArtifacts";
import { jobQueryKey } from "../evidence/usePdfDocument";
import { JOB_STATUS_LABELS } from "../jobStatus";
import { JobResults } from "../results/JobResults";
import { resultsQueryKey } from "../results/useJobResults";

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
    queryKey: jobQueryKey(jobId),
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
        <p className="job-id">
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
        {(job.status === "done" || job.status === "failed") && (
          // key: another job's page starts with the question closed and no delete of its own.
          <DeleteJob key={job.job_id} jobId={job.job_id} displayName={job.display_name} />
        )}
      </section>
      <section aria-labelledby="job-progress-heading" className="card job-section">
        <h2 id="job-progress-heading">Progress</h2>
        <p role="status" className={`job-status-line status-badge status-${job.status}`}>
          Status: {job.status}
        </p>
        <ol className="job-detail-progress" aria-label="Job progress">
          {STAGES.map((status, index) => (
            <li
              key={status}
              aria-current={index === stage ? "step" : undefined}
              className={
                index === stage && job.status === "failed"
                  ? "failed"
                  : index === stage && job.status === "done"
                    ? "done"
                    : undefined
              }
            >
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
        <section aria-labelledby="job-results-heading" className="job-section">
          <h2 id="job-results-heading">Results</h2>
          {/* Keyed by job: the route keeps the page mounted when it moves to another job,
              and the expanded rows and the hide-flagged choice belong to one job. */}
          <JobResults
            key={job.job_id}
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

// The dashboard shows this name once after a delete (see DashboardPage).
export type DeletedJobState = { deletedJob: string };

// A finished job can be deleted. The API keeps the document, the PDF and the artifacts.
function DeleteJob({ jobId, displayName }: { jobId: string; displayName: string | null }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const deleteButton = useRef<HTMLButtonElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const opened = useRef(false);
  useEffect(() => {
    if (confirming) cancelButton.current?.focus();
    else if (opened.current) deleteButton.current?.focus();
  }, [confirming]);
  // The job's cached data goes once the page has unmounted: removed while the page still
  // shows it, a query would load again at the next render.
  const deleted = useRef(false);
  useEffect(
    () => () => {
      if (!deleted.current) return;
      for (const queryKey of [
        jobQueryKey(jobId),
        resultsQueryKey(jobId),
        artifactQueryKey("parser", jobId),
        artifactQueryKey("extraction", jobId),
      ]) {
        queryClient.removeQueries({ queryKey, exact: true });
      }
    },
    [jobId, queryClient],
  );

  // The id to delete is the mutation's variable, fixed when the user confirms. The callbacks
  // here run for that id even if the page has moved on; React Query hands a pending mutation
  // the options of the latest render, so they must not read this render's props.
  const removal = useMutation({
    mutationFn: async (id: string) => {
      try {
        await deleteJob(id);
      } catch (error) {
        // Already deleted, for example in another tab: the outcome is the same.
        if (!(error instanceof ApiError && error.status === 404)) throw error;
      }
    },
    onSuccess: (_data, id) => {
      queryClient.setQueriesData<JobList>({ queryKey: ["jobs"] }, (jobs) =>
        jobs?.filter((listed) => listed.job_id !== id),
      );
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
  function confirmDelete() {
    const state: DeletedJobState = { deletedJob: displayName ?? "Unnamed PDF" };
    removal.mutate(jobId, {
      // Runs only while this job's page is still open: a delete that ends after the user has
      // gone elsewhere does not take them back to the dashboard.
      onSuccess: async () => {
        deleted.current = true;
        // replace: Back does not return to the page of a job that is gone.
        await navigate("/", { replace: true, state });
      },
    });
  }

  if (!confirming) {
    return (
      <p className="job-delete">
        <button
          type="button"
          ref={deleteButton}
          onClick={() => {
            opened.current = true;
            setConfirming(true);
          }}
        >
          Delete job
        </button>
      </p>
    );
  }
  return (
    <div className="job-delete-confirm">
      <p id="job-delete-question">
        Delete this job? It leaves the job lists. The PDF and its stored results are kept.
      </p>
      {removal.isError && (
        <p role="alert">
          {removal.error instanceof ApiError && removal.error.code === "job_not_finished"
            ? "Only a done or failed job can be deleted."
            : "The job could not be deleted. Try again."}
        </p>
      )}
      <p className="job-delete-actions">
        <button
          type="button"
          className="button-danger"
          aria-describedby="job-delete-question"
          disabled={removal.isPending}
          onClick={confirmDelete}
        >
          {removal.isPending ? "Deleting…" : "Delete job"}
        </button>
        <button
          type="button"
          ref={cancelButton}
          aria-describedby="job-delete-question"
          disabled={removal.isPending}
          onClick={() => {
            removal.reset();
            setConfirming(false);
          }}
        >
          Cancel
        </button>
      </p>
    </div>
  );
}

function parseObservationIndex(value: string | null): number | null {
  return value !== null && /^\d+$/.test(value) ? Number(value) : null;
}

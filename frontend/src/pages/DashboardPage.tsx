import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ApiError, getReadiness, type JobList, type JobStatus, listJobs } from "../api/client";
import { shouldRetry } from "../api/queryClient";

const INTERVAL = 5000;
const PAGE_SIZE = 10;
const STATUSES: JobStatus[] = ["queued", "parsing", "parsed", "extracting", "done", "failed"];
const LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  parsing: "Parsing",
  parsed: "Parsed",
  extracting: "Extracting",
  done: "Done",
  failed: "Failed",
};

function useVisible() {
  const [visible, setVisible] = useState(document.visibilityState !== "hidden");
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return visible;
}

function Progress({ job }: { job: JobList[number] }) {
  const parseCompleted =
    ["parsed", "extracting"].includes(job.status) ||
    (job.status === "failed" && (job.has_parse_result || job.error.code === "extraction_failed"));
  const completed = job.status === "done" ? 3 : parseCompleted ? 1 : 0;
  const active = job.status === "parsing" ? 0 : job.status === "extracting" ? 1 : -1;
  return (
    <ol className="job-progress" aria-label={`Progress: ${LABELS[job.status]}`}>
      {["Parse", "Extract", "Done"].map((step, index) => (
        <li
          key={step}
          className={index < completed ? "complete" : index === active ? "active" : ""}
        >
          {index < completed ? "✓ " : index === active ? "● " : "○ "}
          {step}
        </li>
      ))}
    </ol>
  );
}

export function DashboardPage() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const invalidStatus = status !== "" && !STATUSES.includes(status as JobStatus);
  const parser = params.get("parser") ?? "";
  const requestedPage = Number(params.get("page") ?? 1);
  const visible = useVisible();
  const jobs = useQuery({
    queryKey: ["jobs", status, parser],
    queryFn: ({ signal }) => listJobs(status, parser, signal),
    enabled: !invalidStatus,
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: (query) => {
      if (!visible || invalidStatus) return false;
      if (query.state.error) return shouldRetry(0, query.state.error) ? INTERVAL : false;
      return query.state.data?.some((job) => job.status !== "done" && job.status !== "failed")
        ? INTERVAL
        : false;
    },
  });
  const readiness = useQuery({
    queryKey: ["readiness"],
    queryFn: ({ signal }) => getReadiness(signal),
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: visible ? INTERVAL : false,
  });
  const items = jobs.data ?? [];
  const pageCount = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const page = Math.min(
    pageCount,
    Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1,
  );
  function filter(name: string, value: string) {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (value) next.set(name, value);
      else next.delete(name);
      next.delete("page");
      return next;
    });
  }
  function turnPage(value: number) {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("page", String(value));
      return next;
    });
  }
  const unavailable = readiness.data?.unavailable ?? [];
  const unknown = readiness.isError || !readiness.data;
  const grafanaUrl = import.meta.env.VITE_GRAFANA_URL;
  const errorMessage =
    jobs.error instanceof ApiError && jobs.error.code === "job_state_unreadable"
      ? "The job list cannot be read."
      : jobs.error && !shouldRetry(0, jobs.error)
        ? "The job list request was refused. Check the filters and API access."
        : "The job list is temporarily unavailable. Retrying in a few seconds.";

  return (
    <>
      <div className="dashboard-heading">
        <div>
          <h1>Dashboard</h1>
          <p>Follow your document extraction jobs.</p>
        </div>
        <Link className="dashboard-start" to="/jobs/new">
          Start a job
        </Link>
      </div>
      <div className="dashboard-grid">
        <section aria-labelledby="jobs-heading" className="dashboard-jobs">
          <h2 id="jobs-heading">Jobs</h2>
          <div className="dashboard-filters">
            <label>
              Status
              <select value={status} onChange={(event) => filter("status", event.target.value)}>
                <option value="">All statuses</option>
                {!STATUSES.includes(status as JobStatus) && status && (
                  <option value={status}>{status}</option>
                )}
                {STATUSES.map((value) => (
                  <option key={value} value={value}>
                    {LABELS[value]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Parser
              <select value={parser} onChange={(event) => filter("parser", event.target.value)}>
                <option value="">All parsers</option>
                <option value="default">Default</option>
                <option value="mineru">MinerU</option>
                {parser && !["default", "mineru"].includes(parser) && (
                  <option value={parser}>{parser}</option>
                )}
              </select>
            </label>
          </div>
          {invalidStatus && (
            <p role="alert">Unknown status filter. Choose a status from the list.</p>
          )}
          {!invalidStatus && jobs.isPending && <p role="status">Loading jobs…</p>}
          {jobs.isError && (
            <div role="alert">
              <p>{errorMessage}</p>
              <button type="button" onClick={() => void jobs.refetch()}>
                Retry now
              </button>
            </div>
          )}
          {jobs.data && items.length === 0 && (
            <p>
              {status || parser ? (
                "No jobs match these filters."
              ) : (
                <>
                  There are no jobs yet. <Link to="/jobs/new">Start a job</Link>.
                </>
              )}
            </p>
          )}
          {items.length > 0 && (
            <>
              {/* Keyboard focus lets users scroll the table horizontally at narrow widths. */}
              {/* biome-ignore lint/a11y/noNoninteractiveTabindex: scroll regions need keyboard access */}
              <section className="jobs-scroll" aria-label="Job list" tabIndex={0}>
                <table className="jobs-table">
                  <caption>Jobs, newest first</caption>
                  <thead>
                    <tr>
                      {["File", "Status", "Progress", "Parser", "Extractor", "Created"].map(
                        (heading) => (
                          <th key={heading} scope="col">
                            {heading}
                          </th>
                        ),
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {items.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE).map((job) => (
                      <tr key={job.job_id}>
                        <td>
                          <Link to={`/jobs/${encodeURIComponent(job.job_id)}`}>
                            {job.display_name ?? "Unnamed file"}
                          </Link>
                        </td>
                        <td>
                          <span className={`job-status status-${job.status}`}>
                            {LABELS[job.status]}
                          </span>
                          {job.status === "failed" && (
                            <code className="job-error">{job.error.code}</code>
                          )}
                        </td>
                        <td>
                          <Progress job={job} />
                        </td>
                        <td>{job.parser}</td>
                        <td>{job.extractor}</td>
                        <td>
                          <time dateTime={job.created_at}>
                            {new Date(job.created_at).toLocaleString()}
                          </time>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
              <nav aria-label="Job pages" className="job-pagination">
                <button type="button" disabled={page === 1} onClick={() => turnPage(page - 1)}>
                  Previous
                </button>
                <span aria-live="polite">
                  Page {page} of {pageCount} · {items.length} jobs
                </span>
                <button
                  type="button"
                  disabled={page === pageCount}
                  onClick={() => turnPage(page + 1)}
                >
                  Next
                </button>
              </nav>
            </>
          )}
        </section>
        <aside className="dashboard-services" aria-labelledby="services-heading">
          <h2 id="services-heading">Services</h2>
          <p className="services-note">Updates every 5 seconds while this tab is visible.</p>
          <dl>
            {[
              ["api", "API"],
              ["redis", "Redis"],
              ["postgres", "Postgres"],
              ["storage", "Artifact storage"],
            ].map(([key, name]) => {
              const state =
                key === "api"
                  ? readiness.isError
                    ? "Down"
                    : readiness.data
                      ? "Up"
                      : "Unknown"
                  : unknown
                    ? "Unknown"
                    : unavailable.includes(key)
                      ? "Down"
                      : "Up";
              return (
                <div key={key}>
                  <dt>{name}</dt>
                  <dd className={`service-${state.toLowerCase()}`}>{state}</dd>
                </div>
              );
            })}
          </dl>
          {!unknown && unavailable.includes("config") && <p role="alert">Configuration error</p>}
          {grafanaUrl && (
            <a href={grafanaUrl} target="_blank" rel="noreferrer">
              Open Grafana ↗
            </a>
          )}
        </aside>
      </div>
    </>
  );
}

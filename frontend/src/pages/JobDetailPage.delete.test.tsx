import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JobList, JobStatus } from "../api/client";
import { DashboardPage } from "./DashboardPage";
import { JobDetailPage } from "./JobDetailPage";

const metadata = {
  job_id: "abc",
  display_name: "study.pdf",
  parser: "mineru",
  extractor: "default",
  created_at: "2026-11-10T10:00:00Z",
  document_checksum: "a".repeat(64),
};

function job(status: JobStatus, jobId = "abc"): JobList[number] {
  const record = { ...metadata, job_id: jobId, display_name: `${jobId}.pdf` };
  if (status === "failed") {
    return { ...record, status, error: { code: "parse_failed" }, failed_stage: "parsing" };
  }
  if (status === "cancelled") return { ...record, status, cancelled_stage: "queued" };
  return { ...record, status, waiting_for: null, waiting_since: null };
}

const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  for (const client of clients.splice(0)) client.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// The job page and the dashboard share one query client, as in the app.
function renderPage(stored: JobList[number], listed: JobList = [stored]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  clients.push(client);
  client.setQueryData(["jobs", "", ""], listed);
  const router = createMemoryRouter(
    [
      { path: "/", element: <DashboardPage /> },
      { path: "/jobs/:jobId", element: <JobDetailPage /> },
    ],
    { initialEntries: ["/", "/jobs/abc"], initialIndex: 1 },
  );
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { client, router };
}

// GET answers with the job (and an empty result for /ets); DELETE with `deleted`. The job
// list never answers, so the dashboard shows what the cache holds.
function stubApi(stored: JobList[number], deleted: () => Response | Promise<Response>) {
  const fetchMock = vi.fn<typeof fetch>(async (path, init) => {
    const url = String(path);
    if (init?.method === "DELETE") return deleted();
    if (url === "/ready") return Response.json({ status: "ready" });
    if (url.startsWith("/api/jobs?")) return new Promise<Response>(() => {});
    if (url.endsWith("/ets")) return Response.json({ rows: [] });
    // Each job answers as itself: the stored one, or a done job with the requested id.
    const jobId = /^\/api\/jobs\/([^/?]+)/.exec(url)?.[1];
    return Response.json(jobId === stored.job_id ? stored : job("done", jobId));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function deleteCalls(fetchMock: ReturnType<typeof stubApi>) {
  return fetchMock.mock.calls.filter(([, init]) => init?.method === "DELETE");
}

function errorBody(status: number, code: string): Response {
  return Response.json({ detail: { code, message: "Refused" } }, { status });
}

describe("Deleting a job", () => {
  it.each(["queued", "parsing", "parsed", "extracting"] as const)(
    "offers no delete for a %s job",
    async (status) => {
      stubApi(job(status), () => new Response(null, { status: 204 }));
      renderPage(job(status));
      await screen.findByText(`Status: ${status}`);
      expect(screen.queryByRole("button", { name: "Delete job" })).toBeNull();
    },
  );

  it.each(["done", "failed", "cancelled"] as const)(
    "asks before it deletes a %s job",
    async (status) => {
      const fetchMock = stubApi(job(status), () => new Response(null, { status: 204 }));
      renderPage(job(status));

      fireEvent.click(await screen.findByRole("button", { name: "Delete job" }));

      const cancel = screen.getByRole("button", { name: "Cancel" });
      expect(document.activeElement).toBe(cancel);
      expect(cancel.getAttribute("aria-describedby")).toBe("job-delete-question");
      expect(screen.getByText(/The PDF and its stored results are kept/)).toBeDefined();
      expect(deleteCalls(fetchMock)).toHaveLength(0);
    },
  );

  it("keeps the job when the question is cancelled", async () => {
    const fetchMock = stubApi(job("done"), () => new Response(null, { status: 204 }));
    renderPage(job("done"));

    fireEvent.click(await screen.findByRole("button", { name: "Delete job" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Delete job" }));
    expect(screen.queryByText(/stored results are kept/)).toBeNull();
    expect(deleteCalls(fetchMock)).toHaveLength(0);
  });

  it("deletes the job and returns to the dashboard without it", async () => {
    const other = job("done", "other");
    const fetchMock = stubApi(job("done"), () => new Response(null, { status: 204 }));
    const { client, router } = renderPage(job("done"), [job("done"), other]);

    fireEvent.click(await screen.findByRole("button", { name: "Delete job" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete job" }));

    expect(await screen.findByText("Deleted the job for abc.pdf.")).toBeDefined();
    expect(router.state.location.pathname).toBe("/");
    // replace: the job page is no longer in the history.
    expect(router.state.historyAction).toBe("REPLACE");
    expect(deleteCalls(fetchMock).map(([path]) => path)).toEqual(["/api/jobs/abc"]);
    // The cached list drops the job at once, before the list is loaded again.
    expect(screen.getByText("other.pdf")).toBeDefined();
    expect(screen.queryByText("abc.pdf")).toBeNull();
    await waitFor(() => expect(client.getQueryData(["job", "abc"])).toBeUndefined());
    expect(client.getQueryData(["results", "abc"])).toBeUndefined();
    // The job and its results were loaded once and not again after the delete.
    const reads = fetchMock.mock.calls.filter(
      ([path, init]) => String(path).startsWith("/api/jobs/abc") && init?.method !== "DELETE",
    );
    expect(reads.map(([path]) => path)).toEqual(["/api/jobs/abc?detail=full", "/api/jobs/abc/ets"]);
  });

  it("treats a job that is already gone as deleted", async () => {
    stubApi(job("failed"), () => errorBody(404, "job_not_found"));
    const { router } = renderPage(job("failed"));

    fireEvent.click(await screen.findByRole("button", { name: "Delete job" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete job" }));

    expect(await screen.findByText("Deleted the job for abc.pdf.")).toBeDefined();
    expect(router.state.location.pathname).toBe("/");
  });

  it("stays on the job when the API refuses", async () => {
    stubApi(job("done"), () => errorBody(409, "job_not_finished"));
    const { router } = renderPage(job("done"));

    fireEvent.click(await screen.findByRole("button", { name: "Delete job" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete job" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Only a done or failed job can be deleted.",
    );
    expect(router.state.location.pathname).toBe("/jobs/abc");
  });

  it("lets the user try again when the API is unavailable", async () => {
    let answer = () => errorBody(503, "database_unavailable");
    const fetchMock = stubApi(job("done"), () => answer());
    const { router } = renderPage(job("done"));

    fireEvent.click(await screen.findByRole("button", { name: "Delete job" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete job" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "The job could not be deleted. Try again.",
    );

    answer = () => new Response(null, { status: 204 });
    fireEvent.click(screen.getByRole("button", { name: "Delete job" }));
    expect(await screen.findByText("Deleted the job for abc.pdf.")).toBeDefined();
    expect(router.state.location.pathname).toBe("/");
    expect(deleteCalls(fetchMock)).toHaveLength(2);
  });

  describe("when another job opens on the same page", () => {
    // Both jobs are cached, as after visiting them, so the page shows the second at once
    // without unmounting the first job's controls in between.
    function renderTwoJobs() {
      const rendered = renderPage(job("done"), [job("done"), job("done", "def")]);
      rendered.client.setQueryData(["job", "def"], job("done", "def"));
      return rendered;
    }

    it("closes the question", async () => {
      stubApi(job("done"), () => new Response(null, { status: 204 }));
      const { router } = renderTwoJobs();
      fireEvent.click(await screen.findByRole("button", { name: "Delete job" }));
      expect(screen.getByRole("button", { name: "Cancel" })).toBeDefined();

      await act(() => router.navigate("/jobs/def"));

      expect(await screen.findByRole("heading", { name: "def.pdf" })).toBeDefined();
      expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
      expect(screen.getByRole("button", { name: "Delete job" })).toBeDefined();
    });

    it("lets a pending delete finish for the first job only", async () => {
      let finish: (response: Response) => void = () => {};
      const fetchMock = stubApi(
        job("done"),
        () => new Promise<Response>((resolve) => (finish = resolve)),
      );
      const { client, router } = renderTwoJobs();
      fireEvent.click(await screen.findByRole("button", { name: "Delete job" }));
      fireEvent.click(screen.getByRole("button", { name: "Delete job" }));
      expect(await screen.findByRole("button", { name: "Deleting…" })).toBeDefined();

      await act(() => router.navigate("/jobs/def"));
      await screen.findByRole("heading", { name: "def.pdf" });
      await act(async () => finish(new Response(null, { status: 204 })));

      expect(deleteCalls(fetchMock).map(([path]) => path)).toEqual(["/api/jobs/abc"]);
      // The user stays on the second job, and only the first leaves the cached list.
      expect(router.state.location.pathname).toBe("/jobs/def");
      expect(screen.queryByText(/Deleted the job/)).toBeNull();
      await waitFor(() =>
        expect(client.getQueryData<JobList>(["jobs", "", ""])?.map((row) => row.job_id)).toEqual([
          "def",
        ]),
      );
      expect(client.getQueryData(["job", "def"])).toBeDefined();
    });
  });
});

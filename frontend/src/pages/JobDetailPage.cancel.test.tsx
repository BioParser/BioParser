import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JobList, JobStatus } from "../api/client";
import { JobDetailPage } from "./JobDetailPage";

const metadata = {
  job_id: "abc",
  display_name: "study.pdf",
  parser: "mineru",
  extractor: "default",
  created_at: "2026-11-10T10:00:00Z",
  document_checksum: "a".repeat(64),
};

type Stage = "queued" | "parsing" | "parsed" | "extracting";

function job(status: JobStatus): JobList[number] {
  if (status === "failed") {
    return { ...metadata, status, error: { code: "parse_failed" }, failed_stage: "parsing" };
  }
  if (status === "cancelled") return cancelled("queued");
  return { ...metadata, status, waiting_for: null, waiting_since: null };
}

function cancelled(stage: Stage): JobList[number] {
  return { ...metadata, status: "cancelled", cancelled_stage: stage };
}

const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  for (const client of clients.splice(0)) client.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  clients.push(client);
  const router = createMemoryRouter([{ path: "/jobs/:jobId", element: <JobDetailPage /> }], {
    initialEntries: ["/jobs/abc"],
  });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return client;
}

// GET answers with `stored()`, so a test can change what the job is now. POST answers with
// `cancel`.
function stubApi(stored: () => JobList[number], cancel: () => Response) {
  const fetchMock = vi.fn<typeof fetch>(async (path, init) => {
    if (init?.method === "POST") return cancel();
    if (String(path).endsWith("/ets")) return Response.json({ rows: [] });
    return Response.json(stored());
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function cancelCalls(fetchMock: ReturnType<typeof stubApi>) {
  return fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
}

function errorBody(status: number, code: string): Response {
  return Response.json({ detail: { code, message: "Refused" } }, { status });
}

async function confirmCancel() {
  fireEvent.click(await screen.findByRole("button", { name: "Cancel job" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel job" }));
}

describe("Cancelling a job", () => {
  it.each(["done", "failed", "cancelled"] as const)(
    "offers no cancel for a %s job",
    async (status) => {
      stubApi(
        () => job(status),
        () => errorBody(409, "job_already_finished"),
      );
      renderPage();
      await screen.findByText(`Status: ${status}`);
      expect(screen.queryByRole("button", { name: "Cancel job" })).toBeNull();
      expect(screen.getByRole("button", { name: "Delete job" })).toBeDefined();
    },
  );

  it.each(["queued", "parsing", "parsed", "extracting"] as const)(
    "asks before it cancels a %s job",
    async (status) => {
      const fetchMock = stubApi(
        () => job(status),
        () => Response.json(cancelled(status)),
      );
      renderPage();

      fireEvent.click(await screen.findByRole("button", { name: "Cancel job" }));

      const keep = screen.getByRole("button", { name: "Keep the job" });
      expect(document.activeElement).toBe(keep);
      expect(keep.getAttribute("aria-describedby")).toBe("job-cancel-question");
      expect(screen.getByText(/its result is not used/)).toBeDefined();
      expect(cancelCalls(fetchMock)).toHaveLength(0);
    },
  );

  it("keeps the job when the question is dismissed", async () => {
    const fetchMock = stubApi(
      () => job("parsing"),
      () => Response.json(cancelled("parsing")),
    );
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Cancel job" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep the job" }));

    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cancel job" }));
    expect(screen.queryByText(/its result is not used/)).toBeNull();
    expect(cancelCalls(fetchMock)).toHaveLength(0);
    expect(screen.getByText("Status: parsing")).toBeDefined();
  });

  it("shows the cancelled job from the response, without loading it again", async () => {
    // The API would still say parsing: only the cancel's response says cancelled.
    const fetchMock = stubApi(
      () => job("parsing"),
      () => Response.json(cancelled("parsing")),
    );
    const client = renderPage();

    await confirmCancel();

    expect(await screen.findByText("Status: cancelled")).toBeDefined();
    expect(screen.getByText("The job was cancelled while the PDF was being parsed.")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Cancel job" })).toBeNull();
    expect(screen.getByRole("button", { name: "Delete job" })).toBeDefined();
    const stages = within(screen.getByRole("list", { name: "Job progress" })).getAllByRole(
      "listitem",
    );
    const current = stages.find((li) => li.getAttribute("aria-current") === "step");
    expect(current?.textContent).toBe("Parsing — cancelled");
    expect(current?.className).toBe("cancelled");
    expect(cancelCalls(fetchMock).map(([path]) => path)).toEqual(["/api/jobs/abc/cancel"]);
    expect(client.getQueryData(["job", "abc"])).toEqual(cancelled("parsing"));
  });

  it("says the job finished first when the cancel comes too late", async () => {
    let now = job("extracting");
    const fetchMock = stubApi(
      () => now,
      () => {
        now = { ...metadata, status: "done", waiting_for: null, waiting_since: null };
        return errorBody(409, "job_already_finished");
      },
    );
    renderPage();

    await confirmCancel();

    expect(await screen.findByText("Status: done")).toBeDefined();
    expect(
      screen.getByText(
        "The job finished before it could be cancelled, so it keeps its done status.",
      ),
    ).toBeDefined();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel job" })).toBeNull();
    // The page loaded the job again to show how it ended.
    const reads = fetchMock.mock.calls.filter(
      ([path, init]) => path === "/api/jobs/abc?detail=full" && init?.method !== "POST",
    );
    expect(reads.length).toBeGreaterThanOrEqual(2);
  });

  it("keeps the question open with an error when the cancel fails", async () => {
    stubApi(
      () => job("queued"),
      () => errorBody(503, "database_unavailable"),
    );
    renderPage();

    await confirmCancel();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "The job could not be cancelled. Try again.",
    );
    expect(screen.getByRole("button", { name: "Keep the job" })).toBeDefined();
    expect(screen.getByText("Status: queued")).toBeDefined();
  });

  it("shows a job cancelled while it waited to be extracted", async () => {
    stubApi(
      () => cancelled("parsed"),
      () => errorBody(409, "job_already_finished"),
    );
    renderPage();

    await screen.findByText("Status: cancelled");
    expect(
      screen.getByText("The job was cancelled while the job was waiting to be extracted."),
    ).toBeDefined();
    const stages = within(screen.getByRole("list", { name: "Job progress" })).getAllByRole(
      "listitem",
    );
    expect(stages.find((li) => li.getAttribute("aria-current") === "step")?.textContent).toBe(
      "Waiting to extract — cancelled",
    );
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JobStatusResponse } from "../api/client";
import { JobDetailPage } from "./JobDetailPage";

const metadata = {
  job_id: "abc",
  display_name: "study.pdf",
  parser: "mineru",
  extractor: "default",
  created_at: "2026-11-10T10:00:00Z",
  document_checksum: "a".repeat(64),
};
const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  for (const client of clients) client.clear();
  clients.length = 0;
  vi.useRealTimers();
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
  return router;
}

// A done job's page also loads its results, so /ets answers with an empty result.
function answer(path: RequestInfo | URL, job: object): Response {
  return String(path).endsWith("/ets") ? Response.json({ rows: [] }) : Response.json(job);
}

function stubJob(status: JobStatusResponse["status"]) {
  const fetchMock = vi.fn(async (path: RequestInfo | URL) => answer(path, { ...metadata, status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function tick(ms = 10) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("JobDetailPage", () => {
  it.each(["queued", "parsing", "parsed", "extracting", "done"] as const)(
    "shows %s and all five stages",
    async (status) => {
      const fetchMock = stubJob(status);
      renderPage();
      expect((await screen.findByText(`Status: ${status}`)).getAttribute("role")).toBe("status");
      const stages = within(screen.getByRole("list", { name: "Job progress" })).getAllByRole(
        "listitem",
      );
      expect(stages).toHaveLength(5);
      expect(
        stages.find((li) => li.getAttribute("aria-current") === "step")?.textContent?.toLowerCase(),
      ).toBe(status);
      expect(screen.queryByRole("region", { name: "Results" }) !== null).toBe(status === "done");
      // The job, and for a done job its results.
      expect(fetchMock.mock.calls.length).toBe(status === "done" ? 2 : 1);
    },
  );

  it("shows the file, job ID, parser, extractor and creation time", async () => {
    stubJob("queued");
    renderPage();
    await screen.findByRole("heading", { name: "study.pdf" });
    expect(screen.getByText("abc")).toBeDefined();
    expect(screen.getByText("mineru")).toBeDefined();
    expect(screen.getByText("default")).toBeDefined();
    expect(document.querySelector("time")?.dateTime).toBe(metadata.created_at);
  });

  it("asks for the stored record with detail=full", async () => {
    // Without detail=full the API answers only the job ID and the status.
    const fetchMock = vi.fn<typeof fetch>(async (path) =>
      Response.json(
        path === "/api/jobs/abc?detail=full"
          ? { ...metadata, status: "queued" }
          : { job_id: "abc", status: "queued" },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await screen.findByText("Status: queued");
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual(["/api/jobs/abc?detail=full"]);
    expect(screen.getByRole("heading", { name: "study.pdf" })).toBeDefined();
  });

  it("handles an unnamed PDF that failed while extracting", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          ...metadata,
          display_name: null,
          status: "failed",
          failed_stage: "extracting",
          error: { code: "timeout" },
        }),
      ),
    );
    renderPage();
    await screen.findByRole("heading", { name: "Unnamed PDF" });
    const stages = within(screen.getByRole("list", { name: "Job progress" })).getAllByRole(
      "listitem",
    );
    expect(stages.find((li) => li.getAttribute("aria-current") === "step")?.textContent).toBe(
      "Extracting — failed",
    );
  });

  it("loads the next job and cancels an outstanding request when navigating", async () => {
    let firstSignal: AbortSignal | null | undefined;
    const fetchMock = vi.fn<typeof fetch>(async (path, init) => {
      if (path === "/api/jobs/abc?detail=full") {
        firstSignal = init?.signal;
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        });
      }
      return answer(path, {
        ...metadata,
        job_id: "second",
        display_name: "second.pdf",
        status: "done",
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const router = renderPage();
    await act(() => router.navigate("/jobs/second"));
    await screen.findByRole("heading", { name: "second.pdf" });
    expect(firstSignal?.aborted).toBe(true);
    expect(screen.getByText("second")).toBeDefined();
    expect(screen.queryByText("abc")).toBeNull();
  });

  it("does not start a request while the tab is initially hidden", async () => {
    vi.useFakeTimers();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    const fetchMock = stubJob("queued");
    renderPage();
    await tick(6000);
    expect(fetchMock).not.toHaveBeenCalled();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await tick();
    expect(screen.getByRole("status").textContent).toBe("Status: queued");
  });

  it.each([
    ["invalid_pdf", "parsing", "The file could not be read as a valid PDF.", "Parsing — failed"],
    ["parse_failed", "parsing", "The PDF could not be parsed.", "Parsing — failed"],
    [
      "extraction_failed",
      "extracting",
      "Traits could not be extracted from the parsed document.",
      "Extracting — failed",
    ],
    [
      "timeout",
      "parsing",
      "The job took too long while the PDF was being parsed.",
      "Parsing — failed",
    ],
    [
      "internal_error",
      "queued",
      "An unexpected error stopped the job while the job was queued.",
      "Queued — failed",
    ],
    [
      "retries_exhausted",
      "extracting",
      "The job was tried until its attempt limit while traits were being extracted.",
      "Extracting — failed",
    ],
    [
      "deferral_expired",
      "queued",
      "The parser did not answer for an hour, so the job could not start.",
      "Queued — failed",
    ],
    [
      "deferral_expired",
      "parsing",
      "The parser did not answer for an hour, so the job could not start.",
      "Parsing — failed",
    ],
    [
      "deferral_expired",
      "parsed",
      "The extractor did not answer for an hour, so the job could not start.",
      "Waiting to extract — failed",
    ],
    [
      "deferral_expired",
      "extracting",
      "The extractor did not answer for an hour, so the job could not start.",
      "Extracting — failed",
    ],
  ] as const)("explains $code at $stage", async (code, stage, message, step) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          ...metadata,
          status: "failed",
          failed_stage: stage,
          error: { code },
        }),
      ),
    );
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(code);
    expect(alert.textContent).toContain(message);
    const stages = within(screen.getByRole("list", { name: "Job progress" })).getAllByRole(
      "listitem",
    );
    const failedStep = stages.find((li) => li.getAttribute("aria-current") === "step");
    expect(failedStep?.textContent).toBe(step);
  });

  it.each([404, 422])("stops polling and explains an unknown job on %s", async (status) => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => new Response(null, { status }));
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await tick();
    expect(screen.getByRole("alert").textContent).toBe("There is no such job.");
    await tick(12000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(["done", "failed"])("polls until %s and then stops", async (status) => {
    vi.useFakeTimers();
    let response: Record<string, unknown> = {
      ...metadata,
      status: "queued",
      error: { code: "parse_failed" },
    };
    const fetchMock = vi.fn(async (path: RequestInfo | URL) => answer(path, response));
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await tick();
    response =
      status === "failed"
        ? { ...response, status, failed_stage: "parsing" }
        : { ...response, status };
    await tick(3000);
    expect(screen.getByText(`Status: ${status}`).getAttribute("role")).toBe("status");
    const calls = fetchMock.mock.calls.length;
    await tick(12000);
    expect(fetchMock.mock.calls.length).toBe(calls);
  });

  it("pauses in a hidden tab and resumes when visible", async () => {
    vi.useFakeTimers();
    const fetchMock = stubJob("queued");
    renderPage();
    await tick();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    const calls = fetchMock.mock.calls.length;
    await tick(12000);
    expect(fetchMock.mock.calls.length).toBe(calls);
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await tick(3000);
    expect(fetchMock.mock.calls.length).toBeGreaterThan(calls);
  });

  it.each([false, true])("retries a 503 and recovers (cached job: %s)", async (cached) => {
    vi.useFakeTimers();
    let unavailable = !cached;
    const fetchMock = vi.fn(async () =>
      unavailable
        ? new Response(null, { status: 503 })
        : Response.json({ ...metadata, status: "queued" }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await tick();
    if (cached) {
      unavailable = true;
      await tick(3000);
      expect(screen.getByText("study.pdf")).toBeDefined();
    }
    expect(screen.getByRole("alert").textContent).toContain("Retrying automatically");
    unavailable = false;
    await tick(3000);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("Status: queued");
  });
});

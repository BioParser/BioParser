import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JobStatusResponse } from "../api/client";
import { JobDetailPage } from "./JobDetailPage";

const metadata = {
  job_id: "abc",
  display_name: "study.pdf",
  title: null,
  parser: "mineru",
  extractor: "default",
  created_at: "2026-11-10T10:00:00Z",
  document_checksum: "a".repeat(64),
  has_parse_result: false,
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
  it.each([
    { title: "A paper title", display_name: "paper.pdf", expected: "A paper title" },
    { title: "A paper title", display_name: null, expected: "A paper title" },
    { title: null, display_name: "paper.pdf", expected: "paper.pdf" },
    { title: "", display_name: "paper.pdf", expected: "paper.pdf" },
    { title: null, display_name: null, expected: "Unnamed PDF" },
    { title: null, display_name: "", expected: "Unnamed PDF" },
  ])(
    "shows $expected for title=$title and filename=$display_name",
    async ({ title, display_name, expected }) => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          Response.json({
            ...metadata,
            title,
            display_name,
            status: "queued",
          }),
        ),
      );
      renderPage();
      const heading = await screen.findByRole("heading", { name: expected });
      const filename = heading.parentElement?.querySelector(".document-filename");
      expect(filename?.textContent ?? null).toBe(title && display_name ? display_name : null);
    },
  );

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

  it("handles an unnamed PDF and a retained parse result after a timeout", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          ...metadata,
          display_name: null,
          status: "failed",
          has_parse_result: true,
          error: { code: "timeout" },
        }),
      ),
    );
    renderPage();
    await screen.findByRole("heading", { name: "Unnamed PDF" });
    expect(screen.getByRole("alert").textContent).toContain("The parse result is kept.");
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

  it.each(
    [
      ["invalid_pdf", "The file could not be read as a valid PDF."],
      ["parse_failed", "The PDF could not be parsed."],
      ["extraction_failed", "Traits could not be extracted from the parsed document."],
      ["timeout", "The job took too long to complete."],
      ["internal_error", "An unexpected error stopped the job."],
    ].flatMap(([code, message]) =>
      [false, true].map((hasParseResult) => ({ code, message, hasParseResult })),
    ),
  )("explains $code (parse result: $hasParseResult)", async ({ code, message, hasParseResult }) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          ...metadata,
          status: "failed",
          has_parse_result: hasParseResult,
          error: { code },
        }),
      ),
    );
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(code);
    expect(alert.textContent).toContain(message);
    expect(alert.textContent?.includes("The parse result is kept.")).toBe(hasParseResult);
    const stages = within(screen.getByRole("list", { name: "Job progress" })).getAllByRole(
      "listitem",
    );
    const failedStage = stages.find((li) => li.getAttribute("aria-current") === "step");
    expect(failedStage?.textContent).toBe(
      hasParseResult ? "Extracting — failed" : "Parsing — failed",
    );
    expect(stages.filter((li) => li.textContent?.includes("— complete"))).toHaveLength(
      hasParseResult ? 3 : 1,
    );
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
    let response = { ...metadata, status: "queued", error: { code: "parse_failed" } };
    const fetchMock = vi.fn(async (path: RequestInfo | URL) => answer(path, response));
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await tick();
    response = { ...response, status };
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

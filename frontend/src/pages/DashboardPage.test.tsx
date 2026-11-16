import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { JobList, JobStatus } from "../api/client";
import { DashboardPage } from "./DashboardPage";

const clients: QueryClient[] = [];
let jobs: JobList;
let listStatus: number;
let readiness: () => Response;
let listCode: string;
const fetchMock = vi.fn<typeof fetch>();

function job(id: string, status: JobStatus = "done"): JobList[number] {
  const metadata = {
    job_id: id,
    display_name: `${id}.pdf`,
    created_at: "2026-11-06T12:00:00Z",
    parser: "mineru",
    extractor: "default",
    document_checksum: "abc",
    has_parse_result: ["parsed", "extracting", "done", "failed"].includes(status),
  };
  return status === "failed"
    ? { ...metadata, status, error: { code: "extraction_failed" } }
    : { ...metadata, status };
}

function mount(path = "/") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  clients.push(client);
  const router = createMemoryRouter([{ path: "/", element: <DashboardPage /> }], {
    initialEntries: [path],
  });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

function listCalls() {
  return fetchMock.mock.calls.filter(([url]) => String(url).startsWith("/api/jobs"));
}

beforeEach(() => {
  jobs = [job("first")];
  listStatus = 200;
  listCode = "database_unavailable";
  readiness = () => Response.json({ status: "ready" });
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(async (url) =>
    url === "/ready"
      ? readiness()
      : Response.json(
          listStatus === 200 ? jobs : { detail: { code: listCode, message: "Unavailable" } },
          { status: listStatus },
        ),
  );
});

afterEach(() => {
  cleanup();
  for (const client of clients.splice(0)) client.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  fetchMock.mockReset();
});

describe("Dashboard", () => {
  it("keeps parsing complete when extraction fails", async () => {
    jobs = [job("failed", "failed")];
    mount();
    await screen.findByRole("link", { name: "failed.pdf" });
    const steps = within(screen.getByRole("list", { name: "Progress: Failed" })).getAllByRole(
      "listitem",
    );
    expect(steps[0].className).toBe("complete");
    expect(steps[1].className).not.toBe("complete");
    expect(steps[2].className).not.toBe("complete");
  });

  it("does not mark parsing complete when parsing fails", async () => {
    jobs = [
      {
        ...job("failed", "failed"),
        status: "failed",
        has_parse_result: false,
        error: { code: "parse_failed" },
      },
    ];
    mount();
    await screen.findByRole("link", { name: "failed.pdf" });
    const steps = within(screen.getByRole("list", { name: "Progress: Failed" })).getAllByRole(
      "listitem",
    );
    expect(steps.every((step) => step.className !== "complete")).toBe(true);
  });

  it("shows metadata in API order, links to jobs, and handles unnamed failed files", async () => {
    jobs = [job("newer", "failed"), { ...job("older"), display_name: null }];
    mount();
    const link = await screen.findByRole("link", { name: "newer.pdf" });
    expect(link.getAttribute("href")).toBe("/jobs/newer");
    expect(screen.getByRole("link", { name: "Unnamed file" })).toBeDefined();
    expect(screen.getByText("extraction_failed")).toBeDefined();
    expect(screen.getByText("Jobs, newest first")).toBeDefined();
    const rows = screen.getAllByRole("row");
    expect(rows[1].textContent).toContain("newer.pdf");
    expect(rows[2].textContent).toContain("Unnamed file");
    expect(rows[1].textContent).toContain("mineru");
    expect(rows[1].querySelector("time")?.dateTime).toBe("2026-11-06T12:00:00Z");
  });

  it.each(["queued", "parsing", "parsed", "extracting", "done", "failed"] as const)(
    "shows %s with accessible progress",
    async (status) => {
      jobs = [job("one", status)];
      mount();
      await screen.findByRole("link", { name: "one.pdf" });
      const label = status[0].toUpperCase() + status.slice(1);
      expect(screen.getByRole("list", { name: `Progress: ${label}` })).toBeDefined();
    },
  );

  it("reads filters from the URL, sends them to the API, and resets paging when changed", async () => {
    const router = mount("/?status=queued&parser=mineru&page=2");
    await screen.findByRole("link", { name: "first.pdf" });
    expect(listCalls()[0][0]).toBe("/api/jobs?status=queued&parser=mineru");
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "failed" } });
    await screen.findByRole("link", { name: "first.pdf" });
    expect(router.state.location.search).toBe("?status=failed&parser=mineru");
    fireEvent.change(screen.getByLabelText("Parser"), { target: { value: "default" } });
    await screen.findByRole("link", { name: "first.pdf" });
    expect(listCalls().at(-1)?.[0]).toBe("/api/jobs?status=failed&parser=default");
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "" } });
    expect(router.state.location.search).toBe("?parser=default");
  });

  it("pages ten rows at a time without fetching another list", async () => {
    jobs = Array.from({ length: 11 }, (_, i) => job(`job-${i}`));
    const router = mount();
    await screen.findByText("Page 1 of 2 · 11 jobs");
    expect(screen.queryByRole("link", { name: "job-10.pdf" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("link", { name: "job-10.pdf" })).toBeDefined();
    expect(router.state.location.search).toBe("?page=2");
    expect(listCalls()).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(screen.getByRole("link", { name: "job-0.pdf" })).toBeDefined();
  });

  it.each(["-1", "NaN", "1.5", "999"])(
    "handles an invalid or out of range page %s",
    async (page) => {
      mount(`/?page=${page}`);
      await screen.findByText("Page 1 of 1 · 1 jobs");
    },
  );

  it("shows an empty list with a start link", async () => {
    jobs = [];
    mount();
    await screen.findByText(/There are no jobs yet/);
    expect(listCalls()[0][0]).toBe("/api/jobs");
    expect(screen.getAllByRole("link", { name: "Start a job" }).at(-1)?.getAttribute("href")).toBe(
      "/jobs/new",
    );
  });

  it("distinguishes an empty filtered list", async () => {
    jobs = [];
    mount("/?parser=default");
    await screen.findByText("No jobs match these filters.");
    expect(screen.queryByText(/There are no jobs yet/)).toBeNull();
  });

  it("explains unreadable job state", async () => {
    listStatus = 500;
    listCode = "job_state_unreadable";
    mount();
    await screen.findByText("The job list cannot be read.");
  });

  it("marks every service up on a ready response and hides Grafana unless configured", async () => {
    mount();
    const panel = screen.getByRole("complementary");
    await within(panel).findAllByText("Up");
    expect(within(panel).getAllByText("Up")).toHaveLength(4);
    expect(screen.queryByRole("link", { name: /Grafana/ })).toBeNull();
  });

  it.each(["redis", "postgres", "storage", "config"])("shows a 503 for %s", async (check) => {
    readiness = () =>
      Response.json({ detail: { status: "not ready", unavailable: [check] } }, { status: 503 });
    mount();
    const panel = screen.getByRole("complementary");
    await within(panel).findAllByText("Up");
    if (check === "config") {
      expect(within(panel).getByRole("alert").textContent).toBe("Configuration error");
      expect(within(panel).getAllByText("Up")).toHaveLength(1);
      expect(within(panel).getByText("API").parentElement?.textContent).toContain("Up");
      for (const name of ["Redis", "Postgres", "Artifact storage"]) {
        const service = within(panel).getByText(name).parentElement;
        expect(service?.querySelector("dd")?.textContent).toBe("Unknown");
        expect(service?.querySelector("dd")?.className).toBe("service-unknown");
      }
    } else {
      const name = { redis: "Redis", postgres: "Postgres", storage: "Artifact storage" }[check];
      expect(within(panel).getByText(name ?? "").parentElement?.textContent).toContain("Down");
      expect(within(panel).getAllByText("Up")).toHaveLength(3);
    }
  });

  it("marks API down and dependencies unknown when it cannot be reached", async () => {
    readiness = () => {
      throw new TypeError("Failed to fetch");
    };
    mount();
    const panel = screen.getByRole("complementary");
    await within(panel).findByText("Down");
    expect(within(panel).getAllByText("Unknown")).toHaveLength(3);
  });

  it("links to Grafana when configured at build time", async () => {
    vi.stubEnv("VITE_GRAFANA_URL", "http://localhost:3000");
    mount();
    expect(screen.getByRole("link", { name: /Open Grafana/ }).getAttribute("href")).toBe(
      "http://localhost:3000",
    );
    await screen.findByRole("link", { name: "first.pdf" });
  });
});

// Fake timers exercise the actual query scheduling and visibility events.
async function tick(ms = 1) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
}

describe("dashboard refresh", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it("rejects an invalid URL status without fetching jobs and recovers when corrected", async () => {
    mount("/?status=invalid");
    await tick(15000);
    expect(listCalls()).toHaveLength(0);
    expect(screen.getByRole("alert").textContent).toContain("Unknown status filter");
    expect(screen.queryByText(/Loading jobs/)).toBeNull();
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "done" } });
    await tick();
    expect(listCalls()).toHaveLength(1);
    expect(listCalls()[0][0]).toBe("/api/jobs?status=done");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("link", { name: "first.pdf" })).toBeDefined();
  });

  it.each([403, 422])(
    "stops polling on a permanent %s even with running jobs in cache",
    async (status) => {
      jobs = [job("running", "parsing")];
      mount();
      await tick();
      listStatus = status;
      await tick(5000);
      expect(screen.getByRole("alert").textContent).toContain("request was refused");
      expect(screen.getByRole("alert").textContent).not.toContain("Retrying");
      const count = listCalls().length;
      await tick(15000);
      expect(listCalls()).toHaveLength(count);
    },
  );

  it.each([408, 429, 503])("keeps polling after an initial transient %s", async (status) => {
    listStatus = status;
    mount();
    await tick();
    expect(screen.getByRole("alert").textContent).toContain("Retrying");
    await tick(5000);
    expect(listCalls()).toHaveLength(2);
    listStatus = 200;
    await tick(5000);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("link", { name: "first.pdf" })).toBeDefined();
  });

  it("polls the entire list, keeps page and filters, and slows down after all jobs finish", async () => {
    jobs = Array.from({ length: 11 }, (_, i) => job(`job-${i}`, i === 0 ? "parsing" : "done"));
    const router = mount("/?parser=mineru&page=2");
    await tick();
    expect(screen.getByRole("link", { name: "job-10.pdf" })).toBeDefined();
    await tick(5000);
    expect(listCalls()).toHaveLength(2);
    expect(router.state.location.search).toBe("?parser=mineru&page=2");
    jobs = jobs.map((item) => job(item.job_id));
    await tick(5000);
    const count = listCalls().length;
    await tick(15000);
    expect(listCalls()).toHaveLength(count);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/ready").length).toBeGreaterThan(count);
    await tick(15000);
    expect(listCalls()).toHaveLength(count + 1);
    expect(router.state.location.search).toBe("?parser=mineru&page=2");
  });

  it.each(["empty", "done", "failed"] as const)(
    "discovers a new job from an %s list and resumes fast polling",
    async (initial) => {
      jobs = initial === "empty" ? [] : [job("old", initial)];
      mount();
      await tick();
      expect(listCalls()).toHaveLength(1);
      jobs = [job("new", "parsing"), ...jobs];
      await tick(15000);
      expect(listCalls()).toHaveLength(1);
      expect(screen.queryByRole("link", { name: "new.pdf" })).toBeNull();
      await tick(15000);
      expect(listCalls()).toHaveLength(2);
      expect(screen.getByRole("link", { name: "new.pdf" })).toBeDefined();
      await tick(5000);
      expect(listCalls()).toHaveLength(3);
    },
  );

  it.each(["extracting", "done"] as const)(
    "pauses both polls in a hidden tab and resumes when visible with %s jobs",
    async (status) => {
      jobs = [job("one", status)];
      mount();
      await tick();
      vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
      act(() => {
        window.dispatchEvent(new Event("visibilitychange"));
      });
      const count = fetchMock.mock.calls.length;
      const jobCount = listCalls().length;
      const readinessCount = fetchMock.mock.calls.filter(([url]) => url === "/ready").length;
      await tick(60000);
      expect(fetchMock.mock.calls).toHaveLength(count);
      vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
      act(() => {
        window.dispatchEvent(new Event("visibilitychange"));
      });
      await tick(status === "done" ? 30000 : 5000);
      expect(listCalls().length).toBeGreaterThan(jobCount);
      expect(fetchMock.mock.calls.filter(([url]) => url === "/ready").length).toBeGreaterThan(
        readinessCount,
      );
    },
  );

  it("keeps existing rows on a 503 and retries until the list recovers", async () => {
    jobs = [job("running", "parsing")];
    mount();
    await tick();
    listStatus = 503;
    await tick(5000);
    expect(screen.getByRole("alert").textContent).toContain("Retrying");
    expect(screen.getByRole("link", { name: "running.pdf" })).toBeDefined();
    listStatus = 200;
    jobs = [job("finished")];
    await tick(5000);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("link", { name: "finished.pdf" })).toBeDefined();
  });
});

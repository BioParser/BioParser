import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JobList, JobStatusFullResponse } from "../api/client";
import { jobQueryKey } from "../evidence/usePdfDocument";
import { DashboardPage } from "./DashboardPage";
import { JobDetailPage } from "./JobDetailPage";

const stored: JobStatusFullResponse = {
  job_id: "first",
  document_checksum: "a".repeat(64),
  title: "Original title",
  display_name: "paper.pdf",
  parser: "default",
  extractor: "default",
  created_at: "2026-11-28T12:00:00Z",
  status: "done",
};
const peer = { ...stored, job_id: "peer" };
const other = { ...stored, job_id: "other", document_checksum: "b".repeat(64), title: "Other PDF" };
const clients: QueryClient[] = [];

afterEach(() => {
  cleanup();
  for (const client of clients.splice(0)) client.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function mount(
  save: (title: string | null) => Response | Promise<Response> = (title) =>
    Response.json({ checksum: stored.document_checksum, title: title?.trim() || null }),
) {
  let records: JobList = [stored, peer, other];
  const fetchMock = vi.fn<typeof fetch>(async (path, init) => {
    const url = String(path);
    if (init?.method === "PATCH") {
      const response = await save(JSON.parse(String(init.body)).title);
      if (response.ok) {
        const document = await response.clone().json();
        records = records.map((job) =>
          job.document_checksum === document.checksum ? { ...job, title: document.title } : job,
        );
      }
      return response;
    }
    if (url === "/ready") return Response.json({ status: "ready" });
    // A dashboard must show the cached title even while its refresh is waiting.
    if (url.startsWith("/api/jobs?")) return new Promise<Response>(() => {});
    if (url.endsWith("/ets")) return Response.json({ rows: [] });
    const id = /^\/api\/jobs\/([^/?]+)/.exec(url)?.[1];
    return Response.json(records.find((job) => job.job_id === id));
  });
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  clients.push(client);
  client.setQueryData(["jobs", "", ""], records);
  client.setQueryData(["jobs", "done", "default"], [stored, peer]);
  client.setQueryData(jobQueryKey("peer"), peer);
  client.setQueryData(jobQueryKey("other"), other);
  const router = createMemoryRouter(
    [
      { path: "/", element: <DashboardPage /> },
      { path: "/jobs/:jobId", element: <JobDetailPage /> },
    ],
    { initialEntries: ["/jobs/first"] },
  );
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { client, router, fetchMock };
}

async function edit(title: string) {
  fireEvent.click(await screen.findByRole("button", { name: "Edit title" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: title } });
}

describe("Editing a document title", () => {
  it("focuses the labelled field and explains that all jobs share the title", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Edit title" }));
    const input = screen.getByRole("textbox", { name: "Title" }) as HTMLInputElement;
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe("Original title");
    expect(input.maxLength).toBe(500);
    expect(input.getAttribute("aria-describedby")).toBe("document-title-help");
    expect(screen.getByText(/The title applies to every job of this PDF/)).toBeDefined();
  });

  it.each(["Cancel", "Escape"])("discards edits with %s and returns focus", async (action) => {
    const { fetchMock } = mount();
    await edit("Discard this");
    if (action === "Cancel") fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    else fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Edit title" }));
    expect(screen.getByRole("heading", { name: "Original title" })).toBeDefined();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PATCH")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Edit title" }));
    expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("Original title");
  });

  it("shows the saved title on the job page, other jobs and cached dashboards", async () => {
    const { client, router, fetchMock } = mount();
    await edit("  Darwin's paper  ");
    fireEvent.submit(screen.getByRole("textbox").closest("form") as HTMLFormElement);
    await screen.findByText("Title saved.");
    expect(screen.getByRole("heading", { name: "Darwin's paper" })).toBeDefined();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Edit title" }));
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(patch?.[0]).toBe(`/api/documents/${stored.document_checksum}`);
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ title: "  Darwin's paper  " });
    expect(client.getQueryData<JobStatusFullResponse>(jobQueryKey("peer"))?.title).toBe(
      "Darwin's paper",
    );
    expect(client.getQueryData<JobStatusFullResponse>(jobQueryKey("other"))?.title).toBe(
      "Other PDF",
    );
    expect(
      client.getQueryData<JobList>(["jobs", "done", "default"])?.map((job) => job.title),
    ).toEqual(["Darwin's paper", "Darwin's paper"]);
    await act(() => router.navigate("/"));
    expect(screen.getAllByRole("link", { name: "Darwin's paper" })).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Other PDF" })).toBeDefined();
  });

  it.each(["", "   "])("clears a title with a blank value (%s)", async (title) => {
    const { fetchMock } = mount();
    await edit(title);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Title saved.");
    expect(screen.getByRole("heading", { name: "paper.pdf" })).toBeDefined();
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ title: null });
  });

  it("keeps the draft after a failed save and allows a retry", async () => {
    let fails = true;
    mount((title) =>
      fails
        ? new Response(null, { status: 503 })
        : Response.json({ checksum: stored.document_checksum, title }),
    );
    await edit("Keep my draft");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "The title could not be saved. Try again.",
    );
    expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("Keep my draft");
    expect(screen.getByRole("heading", { name: "Original title" })).toBeDefined();
    fails = false;
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("heading", { name: "Keep my draft" });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("locks the form during a save and ignores duplicate submits", async () => {
    let finish: (response: Response) => void = () => {};
    const { fetchMock } = mount(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    await edit("New title");
    const input = screen.getByRole("textbox");
    const form = input.closest("form") as HTMLFormElement;
    fireEvent.submit(form);
    await screen.findByRole("button", { name: "Saving…" });
    expect(input.matches(":disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Cancel" }).matches(":disabled")).toBe(true);
    fireEvent.submit(form);
    fireEvent.keyDown(input, { key: "Escape" });
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PATCH")).toHaveLength(1);
    await act(async () =>
      finish(Response.json({ checksum: stored.document_checksum, title: "New title" })),
    );
    await screen.findByText("Title saved.");
  });

  it("keeps the draft when polling refreshes the job", async () => {
    const { client } = mount();
    await edit("My unsaved title");
    act(() => client.setQueryData(jobQueryKey("first"), { ...stored, title: "Refreshed title" }));
    expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("My unsaved title");
    expect(await screen.findByRole("heading", { name: "Refreshed title" })).toBeDefined();
  });

  it("cancels an older in-flight job read so it cannot overwrite a saved title", async () => {
    const { client, fetchMock } = mount();
    await edit("New title");
    let finishRead: (response: Response) => void = () => {};
    let readSignal: AbortSignal | null | undefined;
    fetchMock.mockImplementationOnce((_path, init) => {
      readSignal = init?.signal;
      return new Promise<Response>((resolve) => {
        finishRead = resolve;
      });
    });
    act(() => {
      void client.refetchQueries({ queryKey: jobQueryKey("first"), exact: true });
    });
    await waitFor(() => expect(readSignal).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Title saved.");
    expect(readSignal?.aborted).toBe(true);
    await act(async () => finishRead(Response.json(stored)));
    expect(screen.getByRole("heading", { name: "New title" })).toBeDefined();
    expect(client.getQueryData<JobStatusFullResponse>(jobQueryKey("first"))?.title).toBe(
      "New title",
    );
  });

  it("does not apply an old job's pending edit to the next PDF after navigation", async () => {
    let finish: (response: Response) => void = () => {};
    const { client, router } = mount(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    await edit("Saved after leaving");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("button", { name: "Saving…" });
    await act(() => router.navigate("/jobs/other"));
    await screen.findByRole("heading", { name: "Other PDF" });
    fireEvent.click(screen.getByRole("button", { name: "Edit title" }));
    await act(async () =>
      finish(Response.json({ checksum: stored.document_checksum, title: "Saved after leaving" })),
    );
    await waitFor(() =>
      expect(client.getQueryData<JobStatusFullResponse>(jobQueryKey("first"))?.title).toBe(
        "Saved after leaving",
      ),
    );
    expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("Other PDF");
    expect(screen.queryByText("Title saved.")).toBeNull();
    expect(router.state.location.pathname).toBe("/jobs/other");
  });
});

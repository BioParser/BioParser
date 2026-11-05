import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, type Mock, vi } from "vitest";
import { clearApiKey, setApiKey } from "../api/apiKey";
import { routes } from "../routes";
import { MAX_UPLOAD_BYTES, NO_PDF_HEADER, NOT_PDF, TOO_LARGE } from "../upload/checkPdf";

// Vitest globals are off, so Testing Library cannot register its cleanup by itself.
afterEach(cleanup);

beforeEach(() => {
  setApiKey("test-key", false);
});

afterEach(() => {
  vi.unstubAllGlobals();
  clearApiKey("forgotten");
  sessionStorage.clear();
  localStorage.clear();
});

function pdf(name: string, content: BlobPart = "%PDF-1.7\n", type = "application/pdf"): File {
  return new File([content], name, { type });
}

// The file in an upload request's form data.
function uploadedFile(init: RequestInit | undefined): File {
  const file = init?.body instanceof FormData ? init.body.get("file") : null;
  if (!(file instanceof File)) {
    throw new Error("The upload has no file");
  }
  return file;
}

// A fake API. Each file is stored under the checksum "sum-<name>" and its job gets the ID
// "job-<name>". upload and start can answer differently for a file.
function stubApi({
  upload = () => undefined,
  start = () => undefined,
}: {
  upload?: (file: File) => Response | undefined;
  start?: (name: string) => Response | undefined;
} = {}): Mock<typeof fetch> {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const path = String(input);
    if (path === "/api/documents") {
      const file = uploadedFile(init);
      return (
        upload(file) ??
        Response.json(
          {
            checksum: `sum-${file.name}`,
            upload_time: "2026-11-05T12:00:00Z",
            size_bytes: file.size,
          },
          { status: 201 },
        )
      );
    }
    const match = /^\/api\/documents\/sum-(.+)\/jobs$/.exec(path);
    if (match?.[1] !== undefined) {
      const name = decodeURIComponent(match[1]);
      return (
        start(name) ?? Response.json({ job_id: `job-${name}`, status: "queued" }, { status: 202 })
      );
    }
    throw new Error(`Unexpected request to ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// The names of the files the page uploaded, in order.
function uploadedNames(fetchMock: Mock<typeof fetch>): string[] {
  return fetchMock.mock.calls
    .filter(([path]) => path === "/api/documents")
    .map(([, init]) => uploadedFile(init).name);
}

function errorBody(status: number, code: string, message: string): Response {
  return Response.json({ detail: { code, message } }, { status });
}

function renderPage(): ReturnType<typeof createMemoryRouter> {
  const router = createMemoryRouter(routes, { initialEntries: ["/jobs/new"] });
  render(<RouterProvider router={router} />);
  return router;
}

function chooseFiles(...files: File[]): void {
  fireEvent.change(screen.getByLabelText("PDF files"), { target: { files } });
}

// Presses Start and waits until every file has a result.
async function startJobs(): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: "Start jobs" }));
  await screen.findByText(/files started a job\.$/);
}

// The status shown next to a file in the list.
function statusOf(name: string): HTMLElement {
  const item = within(screen.getByRole("list", { name: "Files" }))
    .getAllByRole("listitem")
    .find((li) => li.querySelector(".job-file-name")?.textContent === name);
  if (item === undefined) {
    throw new Error(`No file named ${name} in the list`);
  }
  return item;
}

describe("StartJobPage", () => {
  it("starts a job for each file and links to it", async () => {
    const fetchMock = stubApi();
    renderPage();
    chooseFiles(pdf("a.pdf"), pdf("b.pdf"));
    await startJobs();
    expect(uploadedNames(fetchMock)).toEqual(["a.pdf", "b.pdf"]);
    const link = within(statusOf("a.pdf")).getByRole("link", { name: "job-a.pdf" });
    expect(link.getAttribute("href")).toBe("/jobs/job-a.pdf");
    expect(within(statusOf("b.pdf")).getByRole("link").textContent).toBe("job-b.pdf");
    expect(screen.getByRole("status").textContent).toBe("2 of 2 files started a job.");
  });

  it("opens the job page from the link", async () => {
    stubApi();
    const router = renderPage();
    chooseFiles(pdf("a.pdf"));
    await startJobs();
    fireEvent.click(screen.getByRole("link", { name: "job-a.pdf" }));
    expect(router.state.location.pathname).toBe("/jobs/job-a.pdf");
  });

  it("sends the chosen parser and the default extractor", async () => {
    const fetchMock = stubApi();
    renderPage();
    fireEvent.click(screen.getByLabelText("MinerU"));
    chooseFiles(pdf("a.pdf"));
    await startJobs();
    const startCall = fetchMock.mock.calls.find(([path]) => String(path).endsWith("/jobs"));
    expect(JSON.parse(String(startCall?.[1]?.body))).toEqual({
      parser: "mineru",
      extractor: "default",
    });
  });

  it("uses the Default parser unless another is chosen", () => {
    renderPage();
    expect((screen.getByLabelText("Default") as HTMLInputElement).checked).toBe(true);
  });

  it("shows the extractor and the traits in the summary", () => {
    renderPage();
    const summary = screen.getByRole("region", { name: "Summary" });
    expect(within(summary).getByText("Extractor").nextElementSibling?.textContent).toBe("Default");
    expect(within(summary).getByText("Traits").nextElementSibling?.textContent).toBe(
      "Body mass, Body length, Tail length",
    );
  });

  it("does not send files the browser refuses, and sends the others", async () => {
    const fetchMock = stubApi();
    renderPage();
    chooseFiles(
      pdf("notes.txt", "%PDF-1.7", "text/plain"),
      pdf("empty.pdf", ""),
      pdf("good.pdf"),
      pdf("huge.pdf", new Uint8Array(MAX_UPLOAD_BYTES + 1)),
      pdf("fake.pdf", "<html></html>"),
    );
    await startJobs();
    expect(uploadedNames(fetchMock)).toEqual(["good.pdf"]);
    expect(statusOf("notes.txt").textContent).toContain(NOT_PDF);
    expect(statusOf("empty.pdf").textContent).toContain("The file is empty.");
    expect(statusOf("huge.pdf").textContent).toContain(TOO_LARGE);
    expect(statusOf("fake.pdf").textContent).toContain(NO_PDF_HEADER);
    expect(within(statusOf("good.pdf")).getByRole("link").textContent).toBe("job-good.pdf");
    expect(screen.getByRole("status").textContent).toBe("1 of 5 files started a job.");
  });

  it("shows the API's message for each file that fails, and starts the others", async () => {
    stubApi({
      upload: (file) =>
        file.name === "bad-upload.pdf"
          ? errorBody(503, "storage_unavailable", "Storage is unavailable")
          : undefined,
      start: (name) =>
        name === "bad-start.pdf"
          ? errorBody(503, "queue_unavailable", "Job queue is unavailable")
          : undefined,
    });
    renderPage();
    chooseFiles(pdf("bad-upload.pdf"), pdf("good.pdf"), pdf("bad-start.pdf"));
    await startJobs();
    expect(statusOf("bad-upload.pdf").textContent).toContain("Storage is unavailable");
    expect(statusOf("bad-start.pdf").textContent).toContain("Job queue is unavailable");
    expect(within(statusOf("good.pdf")).getByRole("link").textContent).toBe("job-good.pdf");
    expect(screen.getByRole("status").textContent).toBe("1 of 3 files started a job.");
  });

  it("says the file is over 20 MiB for a 413 with a plain-text body", async () => {
    stubApi({
      upload: () =>
        new Response("Request Entity Too Large", {
          status: 413,
          headers: { "Content-Type": "text/plain" },
        }),
    });
    renderPage();
    chooseFiles(pdf("a.pdf"));
    await startJobs();
    expect(statusOf("a.pdf").textContent).toContain(TOO_LARGE);
  });

  it("says the API could not be reached when the request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    renderPage();
    chooseFiles(pdf("a.pdf"));
    await startJobs();
    expect(statusOf("a.pdf").textContent).toContain("The API could not be reached.");
  });

  it("takes files dropped on the page", async () => {
    const fetchMock = stubApi();
    renderPage();
    const zone = screen.getByText(/or drop them here/).parentElement as HTMLElement;
    fireEvent.drop(zone, { dataTransfer: { files: [pdf("a.pdf"), pdf("b.pdf")] } });
    await startJobs();
    expect(uploadedNames(fetchMock)).toEqual(["a.pdf", "b.pdf"]);
  });

  it("cannot start jobs before files are chosen, or again for the same files", async () => {
    stubApi();
    renderPage();
    const button = screen.getByRole("button", { name: "Start jobs" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    chooseFiles(pdf("a.pdf"));
    expect(button.disabled).toBe(false);
    await startJobs();
    expect(button.disabled).toBe(true);
  });
});

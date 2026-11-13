import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, type Mock, vi } from "vitest";
import { clearApiKey, setApiKey } from "../api/apiKey";
import { routes } from "../routes";
import { DEFAULT_MAX_UPLOAD_BYTES, NO_PDF_HEADER, NOT_PDF, tooLarge } from "../upload/checkPdf";

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

// A PDF that reports the given size without allocating that many bytes.
function pdfOfSize(name: string, size: number): File {
  const file = pdf(name);
  Object.defineProperty(file, "size", { value: size });
  return file;
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
// "job-<name>". upload and start can answer differently for a file. maxUploadBytes is the
// limit GET /api/upload-limits reports.
function stubApi({
  upload = () => undefined,
  start = () => undefined,
  maxUploadBytes = DEFAULT_MAX_UPLOAD_BYTES,
}: {
  upload?: (file: File) => Response | undefined | Promise<Response>;
  start?: (name: string) => Response | undefined;
  maxUploadBytes?: number;
} = {}): Mock<typeof fetch> {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    // Like the real fetch: an aborted request is never sent.
    if (init?.signal?.aborted) {
      throw new DOMException("The operation was aborted.", "AbortError");
    }
    const path = String(input);
    if (path === "/api/upload-limits") {
      return Response.json({ max_upload_bytes: maxUploadBytes });
    }
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
      pdfOfSize("huge.pdf", DEFAULT_MAX_UPLOAD_BYTES + 1),
      pdf("fake.pdf", "<html></html>"),
    );
    await startJobs();
    expect(uploadedNames(fetchMock)).toEqual(["good.pdf"]);
    expect(statusOf("notes.txt").textContent).toContain(NOT_PDF);
    expect(statusOf("empty.pdf").textContent).toContain("The file is empty.");
    expect(statusOf("huge.pdf").textContent).toContain(tooLarge(DEFAULT_MAX_UPLOAD_BYTES));
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
    expect(statusOf("a.pdf").textContent).toContain(tooLarge(DEFAULT_MAX_UPLOAD_BYTES));
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

  it("uses the upload limit the API reports", async () => {
    const oneMiB = 1024 * 1024;
    const fetchMock = stubApi({ maxUploadBytes: oneMiB });
    renderPage();
    await screen.findByText(/Up to 1 MiB each\./);
    chooseFiles(pdfOfSize("big.pdf", oneMiB + 1), pdf("small.pdf"));
    await startJobs();
    expect(uploadedNames(fetchMock)).toEqual(["small.pdf"]);
    expect(statusOf("big.pdf").textContent).toContain("The file is over 1 MiB.");
  });

  it("keeps the default limit when the API does not report one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    renderPage();
    expect(screen.getByText(/or drop them here/).textContent).toContain("Up to 20 MiB each.");
  });

  it("stops the batch when the API refuses the key", async () => {
    const fetchMock = stubApi({
      upload: () => errorBody(401, "unauthorized", "Invalid or missing API key"),
    });
    renderPage();
    chooseFiles(pdf("a.pdf"), pdf("b.pdf"), pdf("c.pdf"));
    fireEvent.click(screen.getByRole("button", { name: "Start jobs" }));
    // The key is forgotten, so the key screen replaces the page.
    await waitFor(() => expect(screen.queryByRole("list", { name: "Files" })).toBeNull());
    expect(uploadedNames(fetchMock)).toEqual(["a.pdf"]);
  });

  it("stops the batch when the page is left", async () => {
    let answer: (response: Response) => void = () => {};
    const fetchMock = stubApi({
      upload: () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    });
    const router = renderPage();
    chooseFiles(pdf("a.pdf"), pdf("b.pdf"));
    fireEvent.click(screen.getByRole("button", { name: "Start jobs" }));
    await waitFor(() => expect(uploadedNames(fetchMock)).toEqual(["a.pdf"]));
    await act(() => router.navigate("/"));
    answer(
      Response.json(
        { checksum: "sum-a.pdf", upload_time: "2026-11-05T12:00:00Z", size_bytes: 9 },
        { status: 201 },
      ),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(uploadedNames(fetchMock)).toEqual(["a.pdf"]);
    // An aborted request is never sent, so only the ones that were not aborted count.
    const jobStarts = fetchMock.mock.calls.filter(
      ([path, init]) => String(path).endsWith("/jobs") && !init?.signal?.aborted,
    );
    expect(jobStarts).toEqual([]);
  });

  it("shows a file that cannot be read, sends the rest and releases the form", async () => {
    const fetchMock = stubApi();
    renderPage();
    const unreadable = pdf("gone.pdf");
    unreadable.slice = () =>
      ({
        text: () => Promise.reject(new DOMException("changed", "NotReadableError")),
      }) as unknown as Blob;
    chooseFiles(unreadable, pdf("good.pdf"));
    await startJobs();
    expect(statusOf("gone.pdf").textContent).toContain("The file cannot be read.");
    expect(uploadedNames(fetchMock)).toEqual(["good.pdf"]);
    expect(screen.getByRole("button", { name: "Start jobs" })).toBeTruthy();
  });
});

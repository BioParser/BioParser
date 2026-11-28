import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, type Mock, vi } from "vitest";
import { clearApiKey, setApiKey } from "../api/apiKey";
import { routes } from "../routes";
import { DEFAULT_MAX_UPLOAD_BYTES, NO_PDF_HEADER, NOT_PDF, tooLarge } from "../upload/checkPdf";
import { MAX_FILES_PER_BATCH } from "./StartJobPage";

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
    if (path.startsWith("/api/jobs/")) {
      return new Response(null, { status: 404 });
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
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

function chooseFiles(...files: File[]): void {
  fireEvent.change(screen.getByLabelText("Add PDF files"), { target: { files } });
}

function dropFiles(...files: File[]): void {
  const zone = screen.getByText(/or drop them here/).parentElement as HTMLElement;
  fireEvent.drop(zone, { dataTransfer: { files } });
}

// The names in the file list, in order.
function fileNames(): string[] {
  const list = screen.queryByRole("list", { name: "Files" });
  if (list === null) {
    return [];
  }
  return within(list)
    .getAllByRole("listitem")
    .map((item) => item.querySelector(".job-file-name")?.textContent ?? "");
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

  it("sends the chosen parser and extractor", async () => {
    const fetchMock = stubApi();
    renderPage();
    fireEvent.click(within(screen.getByRole("group", { name: "Parser" })).getByLabelText("MinerU"));
    fireEvent.click(
      within(screen.getByRole("group", { name: "Extractor" })).getByLabelText("Mendel"),
    );
    chooseFiles(pdf("a.pdf"));
    await startJobs();
    const startCall = fetchMock.mock.calls.find(([path]) => String(path).endsWith("/jobs"));
    expect(JSON.parse(String(startCall?.[1]?.body))).toEqual({
      parser: "mineru",
      extractor: "mendel",
    });
  });

  it("uses the Default parser and extractor unless another is chosen", () => {
    renderPage();
    expect(
      (
        within(screen.getByRole("group", { name: "Parser" })).getByLabelText(
          "Default",
        ) as HTMLInputElement
      ).checked,
    ).toBe(true);
    expect(
      (
        within(screen.getByRole("group", { name: "Extractor" })).getByLabelText(
          "Default",
        ) as HTMLInputElement
      ).checked,
    ).toBe(true);
  });

  it("says when to choose each parser", () => {
    renderPage();
    const parsers = within(screen.getByRole("group", { name: "Parser" }));
    expect(parsers.getByLabelText("Default").getAttribute("aria-describedby")).toBe(
      "parser-hint-default",
    );
    expect(document.getElementById("parser-hint-mineru")?.textContent).toContain("OCR");
    expect(parsers.getAllByRole("radio").map((radio) => (radio as HTMLInputElement).value)).toEqual(
      ["default", "mineru"],
    );
  });

  it("shows each file's size in the list", () => {
    stubApi();
    renderPage();
    chooseFiles(pdfOfSize("small.pdf", 300 * 1024), pdfOfSize("big.pdf", 5.5 * 1024 * 1024));
    const sizes = within(screen.getByRole("list", { name: "Files" }))
      .getAllByRole("listitem")
      .map((li) => li.querySelector(".job-file-size")?.textContent);
    expect(sizes).toEqual(["300 KiB", "5.5 MiB"]);
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
    await screen.findByText(/Up to 10 PDFs, 1 MiB each\./);
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
    expect(screen.getByText(/or drop them here/).textContent).toContain(
      "Up to 10 PDFs, 20 MiB each.",
    );
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
      ([path, init]) =>
        String(path).endsWith("/jobs") && init?.method === "POST" && !init.signal?.aborted,
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

  it("adds a second pick to the list instead of replacing the first", async () => {
    const fetchMock = stubApi();
    renderPage();
    chooseFiles(pdf("a.pdf"));
    chooseFiles(pdf("b.pdf"), pdf("c.pdf"));
    expect(fileNames()).toEqual(["a.pdf", "b.pdf", "c.pdf"]);
    await startJobs();
    expect(uploadedNames(fetchMock)).toEqual(["a.pdf", "b.pdf", "c.pdf"]);
    expect(screen.getByRole("status").textContent).toBe("3 of 3 files started a job.");
  });

  it("adds dropped files after the picked ones", () => {
    stubApi();
    renderPage();
    chooseFiles(pdf("a.pdf"));
    dropFiles(pdf("b.pdf"));
    expect(fileNames()).toEqual(["a.pdf", "b.pdf"]);
  });

  it("does not add a file that is already in the list, and says so", () => {
    stubApi();
    renderPage();
    const a = pdf("a.pdf");
    chooseFiles(a);
    chooseFiles(a, pdf("b.pdf"));
    expect(fileNames()).toEqual(["a.pdf", "b.pdf"]);
    expect(screen.getByText("a.pdf is already in the list.")).toBeDefined();
  });

  it("adds a file with the same name when it is a different file", () => {
    stubApi();
    renderPage();
    chooseFiles(pdf("a.pdf", "%PDF-1.7\n"));
    chooseFiles(pdf("a.pdf", "%PDF-1.7\nmore"));
    expect(fileNames()).toEqual(["a.pdf", "a.pdf"]);
  });

  it("removes a file before it is sent, and can add it again", async () => {
    const fetchMock = stubApi();
    renderPage();
    const b = pdf("b.pdf");
    chooseFiles(pdf("a.pdf"), b, pdf("c.pdf"));
    fireEvent.click(screen.getByRole("button", { name: "Remove b.pdf" }));
    expect(fileNames()).toEqual(["a.pdf", "c.pdf"]);
    // Focus moves to the next file's button, so a keyboard user can go on removing.
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Remove c.pdf" }));
    await startJobs();
    expect(uploadedNames(fetchMock)).toEqual(["a.pdf", "c.pdf"]);
    chooseFiles(b);
    expect(fileNames()).toEqual(["a.pdf", "c.pdf", "b.pdf"]);
  });

  it("moves focus to the file input when the last unsent file is removed", () => {
    stubApi();
    renderPage();
    chooseFiles(pdf("a.pdf"));
    fireEvent.click(screen.getByRole("button", { name: "Remove a.pdf" }));
    expect(screen.queryByRole("list", { name: "Files" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByLabelText("Add PDF files"));
  });

  it("empties the list with Clear list", () => {
    stubApi();
    renderPage();
    const clear = screen.getByRole("button", { name: "Clear list" }) as HTMLButtonElement;
    expect(clear.disabled).toBe(true);
    chooseFiles(pdf("a.pdf"), pdf("b.pdf"));
    fireEvent.click(clear);
    expect(screen.queryByRole("list", { name: "Files" })).toBeNull();
    expect(clear.disabled).toBe(true);
  });

  it("keeps at most 10 unsent files and says how many were left out", async () => {
    const fetchMock = stubApi();
    renderPage();
    const twelve = Array.from({ length: 12 }, (_, i) => pdf(`f${i + 1}.pdf`));
    chooseFiles(...twelve);
    expect(fileNames()).toHaveLength(MAX_FILES_PER_BATCH);
    expect(screen.getByText("2 files not added: up to 10 PDFs can wait to be sent.")).toBeDefined();
    chooseFiles(pdf("late.pdf"));
    expect(fileNames()).toHaveLength(MAX_FILES_PER_BATCH);
    // Sent files do not count: after the batch, there is room again.
    await startJobs();
    expect(uploadedNames(fetchMock)).toHaveLength(MAX_FILES_PER_BATCH);
    chooseFiles(pdf("late.pdf"));
    expect(fileNames()).toHaveLength(MAX_FILES_PER_BATCH + 1);
  });

  it("sends only the new files in a second batch", async () => {
    const fetchMock = stubApi();
    renderPage();
    const a = pdf("a.pdf");
    chooseFiles(a);
    await startJobs();
    chooseFiles(pdf("b.pdf"));
    expect(within(statusOf("a.pdf")).getByRole("link").textContent).toBe("job-a.pdf");
    expect(statusOf("b.pdf").textContent).toContain("Not sent yet");
    // The started file has no Remove button, and cannot be added again.
    expect(screen.queryByRole("button", { name: "Remove a.pdf" })).toBeNull();
    chooseFiles(a);
    expect(fileNames()).toEqual(["a.pdf", "b.pdf"]);
    expect(screen.getByText("a.pdf is already in the list.")).toBeDefined();
    await startJobs();
    expect(uploadedNames(fetchMock)).toEqual(["a.pdf", "b.pdf"]);
    expect(screen.getByRole("status").textContent).toBe("2 of 2 files started a job.");
  });

  it("adds a file again whose upload failed, to try once more", async () => {
    let reachable = false;
    const fetchMock = stubApi({
      upload: () =>
        reachable ? undefined : errorBody(503, "storage_unavailable", "Storage is down"),
    });
    renderPage();
    const a = pdf("a.pdf");
    chooseFiles(a);
    await startJobs();
    reachable = true;
    chooseFiles(a);
    await startJobs();
    expect(uploadedNames(fetchMock)).toEqual(["a.pdf", "a.pdf"]);
    expect(screen.getByRole("status").textContent).toBe("1 of 2 files started a job.");
  });

  it("does not change the list while a batch is running", async () => {
    let answer: (response: Response) => void = () => {};
    stubApi({
      upload: () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    });
    renderPage();
    chooseFiles(pdf("a.pdf"), pdf("b.pdf"));
    fireEvent.click(screen.getByRole("button", { name: "Start jobs" }));
    await waitFor(() => expect(statusOf("a.pdf").textContent).toContain("Sending"));
    // The fieldset disables it, which the property does not show but :disabled does.
    expect(screen.getByLabelText("Add PDF files").matches(":disabled")).toBe(true);
    expect((screen.getByRole("button", { name: "Clear list" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(
      (screen.getByRole("button", { name: "Remove b.pdf" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    dropFiles(pdf("c.pdf"));
    expect(fileNames()).toEqual(["a.pdf", "b.pdf"]);
    await act(async () =>
      answer(
        Response.json(
          { checksum: "sum-a.pdf", upload_time: "2026-11-05T12:00:00Z", size_bytes: 9 },
          { status: 201 },
        ),
      ),
    );
  });
});

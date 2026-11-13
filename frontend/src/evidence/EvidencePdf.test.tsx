import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { shouldRetry } from "../api/queryClient";
import { documentError, EvidencePdf, pagesOf } from "./EvidencePdf";
import type { QuotedBlock } from "./quotation";

// pdf.js needs a real browser. Drawing and outlines are tested in PdfPage.test.tsx; here the
// PDF opens, and its pages wait for a width that jsdom never reports.
const openPdf = vi.hoisted(() => vi.fn());
vi.mock("./pdfjs", () => ({ openPdf }));

class NoResizeObserver {
  observe() {}
  disconnect() {}
}

// Vitest globals are off, so Testing Library cannot register its cleanup by itself.
afterEach(cleanup);

beforeEach(() => {
  openPdf.mockResolvedValue({ getPage: vi.fn() });
  vi.stubGlobal("ResizeObserver", NoResizeObserver);
  stubApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
  openPdf.mockReset();
});

const BOX = { x0: 0.1, y0: 0.2, x1: 0.9, y1: 0.3 };

function block(blockId: string, pageNumber: number, bbox: QuotedBlock["bbox"] = BOX): QuotedBlock {
  return { blockId, pageNumber, bbox, segments: [] };
}

function error(status: number, code: string): Response {
  return Response.json({ detail: { code, message: code } }, { status });
}

function stubApi(
  job: () => Response = () => Response.json({ job_id: "job-1", document_checksum: "abc" }),
  file: () => Response = () => new Response(new Uint8Array([37, 80, 68, 70])),
) {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input) => (String(input).endsWith("/file") ? file() : job())),
  );
}

function renderPdf(quotation: QuotedBlock[]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: shouldRetry, retryDelay: 0 } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <EvidencePdf jobId="job-1" quotation={quotation} />
    </QueryClientProvider>,
  );
}

describe("pagesOf", () => {
  it("outlines a block on its page", () => {
    expect(pagesOf([block("b0", 1)])).toEqual([
      { pageNumber: 1, outlines: [{ blockId: "b0", bbox: BOX }], unplaced: [] },
    ]);
  });

  it("puts two blocks on one page together", () => {
    const pages = pagesOf([block("b3", 2), block("b4", 2)]);
    expect(pages).toHaveLength(1);
    expect(pages[0]?.outlines.map((outline) => outline.blockId)).toEqual(["b3", "b4"]);
  });

  it("keeps two pages in the order of the quotation", () => {
    expect(pagesOf([block("b8", 3), block("b9", 4)]).map((page) => page.pageNumber)).toEqual([
      3, 4,
    ]);
  });

  it("does not outline a block without a box", () => {
    expect(pagesOf([block("b1", 1, null)])).toEqual([
      { pageNumber: 1, outlines: [], unplaced: ["b1"] },
    ]);
  });
});

describe("EvidencePdf", () => {
  it("shows each page of the quotation", async () => {
    renderPdf([block("b8", 3), block("b9", 4)]);
    expect(await screen.findByRole("img", { name: "Page 3 of the PDF" })).toBeDefined();
    expect(screen.getByRole("img", { name: "Page 4 of the PDF" })).toBeDefined();
  });

  it("shows the page of a block without a box and says why it has no outline", async () => {
    renderPdf([block("b1", 1, null)]);
    expect(await screen.findByRole("img", { name: "Page 1 of the PDF" })).toBeDefined();
    expect(screen.getByText(/gave no position/).textContent).toBe(
      "The parser gave no position for block b1, so it is not outlined.",
    );
  });

  it("says the PDF is loading", () => {
    renderPdf([block("b0", 1)]);
    expect(screen.getByRole("status").textContent).toBe("Loading the PDF…");
  });

  it("says when the PDF is missing", async () => {
    stubApi(undefined, () => error(404, "document_not_found"));
    renderPdf([block("b0", 1)]);
    expect((await screen.findByRole("alert")).textContent).toBe("The PDF of this job is missing.");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("says when pdf.js cannot open the PDF", async () => {
    openPdf.mockRejectedValue(new Error("Invalid PDF structure."));
    renderPdf([block("b0", 1)]);
    expect((await screen.findByRole("alert")).textContent).toBe(
      "The PDF of this job cannot be opened. The file may be damaged.",
    );
  });

  it("says when the job does not exist", async () => {
    stubApi(() => error(404, "job_not_found"));
    renderPdf([block("b0", 1)]);
    expect((await screen.findByRole("alert")).textContent).toBe("This job does not exist.");
  });
});

describe("documentError", () => {
  it("says the stored PDF cannot be read for a 500", () => {
    expect(documentError(new ApiError(500, "artifact_invalid", "x"), false)).toBe(
      "The stored PDF of this job cannot be read.",
    );
  });

  it("uses the API's message for another error", () => {
    expect(documentError(new ApiError(503, "storage_unavailable", "Storage is down"), false)).toBe(
      "Storage is down",
    );
  });

  it("says the connection failed for a network error", () => {
    expect(documentError(new TypeError("Failed to fetch"), false)).toBe(
      "The PDF could not be loaded. Check the connection and reopen the observation.",
    );
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, type Mock, vi } from "vitest";
import { ApiError } from "../api/client";
import { shouldRetry } from "../api/queryClient";
import { retryDocument, usePdfDocument } from "./usePdfDocument";

// pdf.js needs a real browser; these tests check what is fetched and when.
const openPdf = vi.hoisted(() => vi.fn());
vi.mock("./pdfjs", () => ({ openPdf }));

const CHECKSUM = "a".repeat(64);
const PDF = { numPages: 3 };

// Vitest globals are off, so Testing Library cannot register its cleanup by itself.
afterEach(cleanup);

beforeEach(() => {
  openPdf.mockResolvedValue(PDF);
});

afterEach(() => {
  vi.unstubAllGlobals();
  openPdf.mockReset();
});

function job(): Response {
  return Response.json({ job_id: "job-1", status: "done", document_checksum: CHECKSUM });
}

function error(status: number, code: string): Response {
  return Response.json({ detail: { code, message: code } }, { status });
}

function stubApi(
  jobResponse: () => Response = job,
  file: () => Response = () => new Response(new Uint8Array([37, 80, 68, 70])),
): Mock<typeof fetch> {
  const fetchMock = vi.fn<typeof fetch>(async (input) =>
    String(input).endsWith("/file") ? file() : jobResponse(),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function paths(fetchMock: Mock<typeof fetch>): string[] {
  return fetchMock.mock.calls.map(([input]) => String(input));
}

function wrapperFor(queryClient: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

function newQueryClient(): QueryClient {
  // The app's retry rule, with no delay between retries so a retry settles within waitFor.
  return new QueryClient({ defaultOptions: { queries: { retry: shouldRetry, retryDelay: 0 } } });
}

describe("usePdfDocument", () => {
  it("fetches nothing until it is enabled", async () => {
    const fetchMock = stubApi();
    const { result } = renderHook(() => usePdfDocument("job-1", false), {
      wrapper: wrapperFor(newQueryClient()),
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(openPdf).not.toHaveBeenCalled();
    expect(result.current.pdf.fetchStatus).toBe("idle");
  });

  it("opens the job's PDF", async () => {
    const fetchMock = stubApi();
    const { result } = renderHook(() => usePdfDocument("job-1", true), {
      wrapper: wrapperFor(newQueryClient()),
    });
    await waitFor(() => expect(result.current.pdf.isSuccess).toBe(true));
    expect(result.current.pdf.data).toBe(PDF);
    expect(paths(fetchMock)).toEqual(["/api/jobs/job-1", `/api/documents/${CHECKSUM}/file`]);
    expect([...new Uint8Array(openPdf.mock.calls[0]?.[0])]).toEqual([37, 80, 68, 70]);
  });

  it("opens the PDF once for every observation of the job", async () => {
    const fetchMock = stubApi();
    const wrapper = wrapperFor(newQueryClient());
    const first = renderHook(() => usePdfDocument("job-1", true), { wrapper });
    await waitFor(() => expect(first.result.current.pdf.isSuccess).toBe(true));
    first.unmount();
    const second = renderHook(() => usePdfDocument("job-1", true), { wrapper });
    expect(second.result.current.pdf.data).toBe(PDF);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(openPdf).toHaveBeenCalledTimes(1);
  });

  it("does not fetch the PDF when the job cannot be loaded", async () => {
    const fetchMock = stubApi(() => error(404, "job_not_found"));
    const { result } = renderHook(() => usePdfDocument("job-1", true), {
      wrapper: wrapperFor(newQueryClient()),
    });
    await waitFor(() => expect(result.current.checksum.isError).toBe(true));
    expect(paths(fetchMock)).toEqual(["/api/jobs/job-1"]);
    expect(result.current.pdf.fetchStatus).toBe("idle");
  });

  it("does not retry a missing document", async () => {
    const fetchMock = stubApi(undefined, () => error(404, "document_not_found"));
    const { result } = renderHook(() => usePdfDocument("job-1", true), {
      wrapper: wrapperFor(newQueryClient()),
    });
    await waitFor(() => expect(result.current.pdf.isError).toBe(true));
    expect(result.current.pdf.error).toMatchObject({ status: 404 });
    expect(paths(fetchMock).filter((path) => path.endsWith("/file"))).toHaveLength(1);
  });

  it("does not retry a PDF that pdf.js cannot open", async () => {
    stubApi();
    openPdf.mockRejectedValue(new Error("Invalid PDF structure."));
    const { result } = renderHook(() => usePdfDocument("job-1", true), {
      wrapper: wrapperFor(newQueryClient()),
    });
    await waitFor(() => expect(result.current.pdf.isError).toBe(true));
    expect(openPdf).toHaveBeenCalledTimes(1);
  });
});

describe("retryDocument", () => {
  it("retries a storage outage", () => {
    expect(retryDocument(0, new ApiError(503, "storage_unavailable", "down"))).toBe(true);
  });

  it("retries a network failure", () => {
    expect(retryDocument(0, new TypeError("Failed to fetch"))).toBe(true);
  });

  it("does not retry an unreadable stored file", () => {
    expect(retryDocument(0, new ApiError(500, "artifact_invalid", "broken"))).toBe(false);
  });

  it("does not retry a PDF that pdf.js refused", () => {
    expect(retryDocument(0, new Error("Invalid PDF structure."))).toBe(false);
  });
});

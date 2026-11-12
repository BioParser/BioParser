import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, type Mock, vi } from "vitest";
import { ApiError, type ExtractionArtifact, type ParserArtifact } from "../api/client";
import { retryArtifact, useEvidenceArtifacts } from "./useEvidenceArtifacts";

const VALUE = {
  kind: "citation" as const,
  text: "12.5",
  evidence: [{ block_id: "b0", start: 21, end: 25 }],
};
const UNIT = {
  kind: "citation" as const,
  text: "kg",
  evidence: [{ block_id: "b0", start: 26, end: 28 }],
};

const PARSER: ParserArtifact = {
  schema_version: "1",
  checksum: "abc",
  parser: { name: "default", version: "1", configuration: {} },
  pages: [
    {
      page_number: 1,
      width: 612,
      height: 792,
      blocks: [
        {
          block_id: "b0",
          order: 0,
          kind: "text",
          role: "body",
          content: { type: "text", text: "Mus musculus weighed 12.5 kg" },
        },
      ],
    },
  ],
};

const EXTRACTION: ExtractionArtifact = {
  schema_version: "1",
  extractor: "default",
  extractor_version: "1",
  observations: [
    {
      trait: { kind: "interpretation", value: "body_mass", passage: VALUE },
      verbatimScientificName: VALUE,
      verbatimTraitValue: VALUE,
      verbatimTraitUnit: UNIT,
    },
  ],
};

function error(status: number, code: string): Response {
  return Response.json({ detail: { code, message: code } }, { status });
}

// Answers each artifact route with a fresh response, since a body can be read only once.
function stubArtifacts(
  parser: () => Response = () => Response.json(PARSER),
  extraction: () => Response = () => Response.json(EXTRACTION),
): Mock<typeof fetch> {
  const fetchMock = vi.fn<typeof fetch>(async (input) =>
    String(input).endsWith("/parser") ? parser() : extraction(),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function wrapperFor(queryClient: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

function newQueryClient(): QueryClient {
  // No delay between retries, so a retried request settles within waitFor.
  return new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } });
}

// Vitest globals are off, so Testing Library cannot register its cleanup by itself.
afterEach(cleanup);
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useEvidenceArtifacts", () => {
  it("fetches nothing until it is enabled", async () => {
    const fetchMock = stubArtifacts();
    const { result } = renderHook(() => useEvidenceArtifacts("job-1", false), {
      wrapper: wrapperFor(newQueryClient()),
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.blocks.fetchStatus).toBe("idle");
  });

  it("indexes the parser blocks and returns the observations", async () => {
    stubArtifacts();
    const { result } = renderHook(() => useEvidenceArtifacts("job-1", true), {
      wrapper: wrapperFor(newQueryClient()),
    });
    await waitFor(() => expect(result.current.observations.isSuccess).toBe(true));
    await waitFor(() => expect(result.current.blocks.isSuccess).toBe(true));
    expect(result.current.blocks.data?.get("b0")?.pageNumber).toBe(1);
    expect(result.current.observations.data).toHaveLength(1);
  });

  it("fetches each artifact once per job", async () => {
    const fetchMock = stubArtifacts();
    const wrapper = wrapperFor(newQueryClient());
    const first = renderHook(() => useEvidenceArtifacts("job-1", true), { wrapper });
    await waitFor(() => expect(first.result.current.blocks.isSuccess).toBe(true));
    await waitFor(() => expect(first.result.current.observations.isSuccess).toBe(true));
    // Another observation of the same job opens the view again.
    first.unmount();
    const second = renderHook(() => useEvidenceArtifacts("job-1", true), { wrapper });
    expect(second.result.current.blocks.isSuccess).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps the parser blocks when the extraction artifact is missing", async () => {
    stubArtifacts(undefined, () => error(409, "artifact_not_available"));
    const { result } = renderHook(() => useEvidenceArtifacts("job-1", true), {
      wrapper: wrapperFor(newQueryClient()),
    });
    await waitFor(() => expect(result.current.observations.isError).toBe(true));
    await waitFor(() => expect(result.current.blocks.isSuccess).toBe(true));
    expect(result.current.observations.error).toMatchObject({ status: 409 });
  });

  it("does not retry an artifact the API cannot read", async () => {
    const fetchMock = stubArtifacts(() => error(500, "artifact_invalid"));
    const { result } = renderHook(() => useEvidenceArtifacts("job-1", true), {
      wrapper: wrapperFor(newQueryClient()),
    });
    await waitFor(() => expect(result.current.blocks.isError).toBe(true));
    const parserCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/parser"));
    expect(parserCalls).toHaveLength(1);
  });
});

describe("retryArtifact", () => {
  it("retries a storage outage", () => {
    expect(retryArtifact(0, new ApiError(503, "storage_unavailable", "down"))).toBe(true);
  });

  it("does not retry a missing artifact", () => {
    expect(retryArtifact(0, new ApiError(409, "artifact_not_available", "not yet"))).toBe(false);
  });

  it("does not retry an unreadable artifact", () => {
    expect(retryArtifact(0, new ApiError(500, "artifact_invalid", "broken"))).toBe(false);
  });
});
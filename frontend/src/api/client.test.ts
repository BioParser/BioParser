import { afterEach, describe, expect, it, type Mock, vi } from "vitest";
import { clearApiKey, getApiKey, getRemovedReason, setApiKey } from "./apiKey";
import {
  ApiError,
  apiFetch,
  getDocumentFile,
  getExtractionArtifact,
  getJob,
  getParserArtifact,
  startDocumentJob,
  uploadDocument,
} from "./client";

function stubFetch(response: Response): Mock<typeof fetch> {
  const fetchMock = vi.fn<typeof fetch>(async () => response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// The headers of the first request the stubbed fetch received.
function sentHeaders(fetchMock: Mock<typeof fetch>): Headers {
  return new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
}

function unauthorized(): Response {
  return Response.json(
    { detail: { code: "unauthorized", message: "Invalid or missing API key" } },
    { status: 401 },
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearApiKey("forgotten");
  sessionStorage.clear();
  localStorage.clear();
});

describe("apiFetch", () => {
  it("returns the JSON body", async () => {
    stubFetch(Response.json({ job_id: "abc", status: "queued" }));
    await expect(getJob("abc")).resolves.toEqual({ job_id: "abc", status: "queued" });
  });

  it("throws ApiError with the API's error code", async () => {
    stubFetch(
      Response.json(
        { detail: { code: "job_not_found", message: "Job not found" } },
        { status: 404 },
      ),
    );
    await expect(getJob("abc")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      code: "job_not_found",
      message: "Job not found",
    });
  });

  it("throws ApiError without a code for FastAPI's validation body", async () => {
    stubFetch(Response.json({ detail: [{ msg: "field required" }] }, { status: 422 }));
    const error = await getJob("abc").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: undefined });
  });

  it("throws ApiError for a body that is not JSON", async () => {
    stubFetch(new Response("Bad Gateway", { status: 502 }));
    await expect(getJob("abc")).rejects.toMatchObject({ status: 502, code: undefined });
  });
});

describe("apiFetch and the API key", () => {
  it("sends the stored key as X-API-Key", async () => {
    setApiKey("the-key", false);
    const fetchMock = stubFetch(Response.json({}));
    await getJob("abc");
    expect(sentHeaders(fetchMock).get("X-API-Key")).toBe("the-key");
  });

  it("sends no X-API-Key without a stored key", async () => {
    const fetchMock = stubFetch(Response.json({}));
    await getJob("abc");
    expect(sentHeaders(fetchMock).has("X-API-Key")).toBe(false);
  });

  it("keeps the caller's own headers", async () => {
    setApiKey("the-key", false);
    const fetchMock = stubFetch(Response.json({}));
    await apiFetch("/api/anything", { headers: { Accept: "application/json" } });
    expect(sentHeaders(fetchMock).get("Accept")).toBe("application/json");
    expect(sentHeaders(fetchMock).get("X-API-Key")).toBe("the-key");
  });

  it("forgets the key on a 401 and records that it was rejected", async () => {
    setApiKey("wrong-key", true);
    stubFetch(unauthorized());
    await expect(getJob("abc")).rejects.toMatchObject({ status: 401, code: "unauthorized" });
    expect(getApiKey()).toBeNull();
    expect(getRemovedReason()).toBe("rejected");
  });

  it("keeps the earlier reason on a 401 when no key was sent", async () => {
    clearApiKey("forgotten");
    stubFetch(unauthorized());
    await expect(getJob("abc")).rejects.toMatchObject({ status: 401 });
    expect(getRemovedReason()).toBe("forgotten");
  });

  it("keeps a new key when a 401 arrives for the old one", async () => {
    setApiKey("old-key", false);
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () => {
        // The user enters a new key while the request with the old one is on its way.
        setApiKey("new-key", false);
        return unauthorized();
      }),
    );
    await expect(getJob("abc")).rejects.toMatchObject({ status: 401 });
    expect(getApiKey()).toBe("new-key");
  });

  it("keeps the key on other errors", async () => {
    setApiKey("the-key", false);
    stubFetch(new Response("Bad Gateway", { status: 502 }));
    await expect(getJob("abc")).rejects.toMatchObject({ status: 502 });
    expect(getApiKey()).toBe("the-key");
  });

  // The path and options of the first request the stubbed fetch received.
  function sentRequest(fetchMock: Mock<typeof fetch>): { path: unknown; init: RequestInit } {
    const [path, init] = fetchMock.mock.calls[0] ?? [];
    return { path, init: init ?? {} };
  }

  describe("uploadDocument", () => {
    it("posts the file as multipart form data", async () => {
      const fetchMock = stubFetch(Response.json({ checksum: "abc" }, { status: 201 }));
      const file = new File(["%PDF-1.7"], "paper.pdf", { type: "application/pdf" });
      await expect(uploadDocument(file)).resolves.toMatchObject({ checksum: "abc" });
      const { path, init } = sentRequest(fetchMock);
      expect(path).toBe("/api/documents");
      expect(init.method).toBe("POST");
      expect(init.body).toBeInstanceOf(FormData);
      expect((init.body as FormData).get("file")).toBe(file);
    });
  });

  describe("startDocumentJob", () => {
    it("posts the parser and extractor as JSON", async () => {
      const fetchMock = stubFetch(
        Response.json({ job_id: "job-1", status: "queued" }, { status: 202 }),
      );
      await expect(
        startDocumentJob("abc", { parser: "mineru", extractor: "default" }),
      ).resolves.toEqual({ job_id: "job-1", status: "queued" });
      const { path, init } = sentRequest(fetchMock);
      expect(path).toBe("/api/documents/abc/jobs");
      expect(init.method).toBe("POST");
      expect(sentHeaders(fetchMock).get("Content-Type")).toBe("application/json");
      expect(JSON.parse(String(init.body))).toEqual({ parser: "mineru", extractor: "default" });
    });
  });

  describe("job artifacts", () => {
    it("gets the parser artifact of the job", async () => {
      const fetchMock = stubFetch(Response.json({ pages: [] }));
      await expect(getParserArtifact("job/1")).resolves.toEqual({ pages: [] });
      expect(sentRequest(fetchMock).path).toBe("/api/jobs/job%2F1/artifacts/parser");
    });

    it("gets the extraction artifact of the job", async () => {
      const fetchMock = stubFetch(Response.json({ observations: [] }));
      await expect(getExtractionArtifact("job/1")).resolves.toEqual({ observations: [] });
      expect(sentRequest(fetchMock).path).toBe("/api/jobs/job%2F1/artifacts/extraction");
    });

    it("passes the abort signal on", async () => {
      const fetchMock = stubFetch(Response.json({ pages: [] }));
      const controller = new AbortController();
      await getParserArtifact("job-1", controller.signal);
      expect(sentRequest(fetchMock).init.signal).toBe(controller.signal);
    });

    describe("getDocumentFile", () => {
      it("returns the PDF's bytes and sends the key", async () => {
        setApiKey("the-key", false);
        const fetchMock = stubFetch(new Response(new Uint8Array([37, 80, 68, 70])));
        const bytes = await getDocumentFile("abc");
        expect([...new Uint8Array(bytes)]).toEqual([37, 80, 68, 70]);
        expect(sentRequest(fetchMock).path).toBe("/api/documents/abc/file");
        expect(sentHeaders(fetchMock).get("X-API-Key")).toBe("the-key");
      });

      it("throws ApiError for a missing document", async () => {
        stubFetch(
          Response.json(
            { detail: { code: "document_not_found", message: "Document not found" } },
            { status: 404 },
          ),
        );
        await expect(getDocumentFile("abc")).rejects.toMatchObject({
          status: 404,
          code: "document_not_found",
        });
      });

      it("forgets a rejected key", async () => {
        setApiKey("the-key", false);
        stubFetch(unauthorized());
        await expect(getDocumentFile("abc")).rejects.toMatchObject({ status: 401 });
        expect(getApiKey()).toBeNull();
      });
    });
  });
});

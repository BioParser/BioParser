import { afterEach, describe, expect, it, type Mock, vi } from "vitest";
import { clearApiKey, getApiKey, getRemovedReason, setApiKey } from "./apiKey";
import { ApiError, apiFetch, getJob } from "./client";

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
});
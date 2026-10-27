import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getJob } from "./client";

function stubFetch(response: Response): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => response),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
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

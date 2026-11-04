import { afterEach, describe, expect, it, vi } from "vitest";
import { changeKeyInAnotherTab } from "./anotherTab";
import { clearApiKey, setApiKey } from "./apiKey";
import { ApiError, getJob } from "./client";
import { createQueryClient, shouldRetry } from "./queryClient";

const JOB_KEY = ["job", "abc"];

afterEach(() => {
  vi.unstubAllGlobals();
  clearApiKey("forgotten");
  sessionStorage.clear();
  localStorage.clear();
});

describe("shouldRetry", () => {
  it("does not retry a 401", () => {
    const error = new ApiError(401, "unauthorized", "Invalid or missing API key");
    expect(shouldRetry(0, error)).toBe(false);
  });

  it("retries other API errors up to three times", () => {
    const error = new ApiError(503, "database_unavailable", "Database unavailable");
    expect(shouldRetry(0, error)).toBe(true);
    expect(shouldRetry(2, error)).toBe(true);
    expect(shouldRetry(3, error)).toBe(false);
  });

  it("retries a network failure up to three times", () => {
    const error = new TypeError("Failed to fetch");
    expect(shouldRetry(0, error)).toBe(true);
    expect(shouldRetry(3, error)).toBe(false);
  });
});

describe("createQueryClient", () => {
  it("uses shouldRetry for every query", () => {
    const queryClient = createQueryClient();
    expect(queryClient.getDefaultOptions().queries?.retry).toBe(shouldRetry);
  });

  it("keeps cached data when a key is set", () => {
    const queryClient = createQueryClient();
    queryClient.setQueryData(JOB_KEY, { status: "queued" });
    setApiKey("the-key", false);
    expect(queryClient.getQueryData(JOB_KEY)).toEqual({ status: "queued" });
  });

  it("drops cached data when the key is forgotten", () => {
    setApiKey("the-key", false);
    const queryClient = createQueryClient();
    queryClient.setQueryData(JOB_KEY, { status: "queued" });
    clearApiKey("forgotten");
    expect(queryClient.getQueryData(JOB_KEY)).toBeUndefined();
  });

  it("drops cached data when a request gets a 401", async () => {
    setApiKey("wrong-key", false);
    const queryClient = createQueryClient();
    queryClient.setQueryData(JOB_KEY, { status: "queued" });
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () => new Response(null, { status: 401 })),
    );
    await getJob("abc").catch(() => undefined);
    expect(queryClient.getQueryData(JOB_KEY)).toBeUndefined();
  });

  it("drops cached data when another tab removes the key", () => {
    setApiKey("shared-key", true);
    const queryClient = createQueryClient();
    queryClient.setQueryData(JOB_KEY, { status: "queued" });
    changeKeyInAnotherTab(null);
    expect(queryClient.getQueryData(JOB_KEY)).toBeUndefined();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, type Mock, vi } from "vitest";
import { clearApiKey, setApiKey } from "../api/apiKey";
import { routes } from "../routes";

// Vitest globals are off, so Testing Library cannot register its cleanup by itself.
afterEach(cleanup);

let fetchMock: Mock<typeof fetch>;

beforeEach(() => {
  setApiKey("test-key", false);
  // Both artifact routes answer 409, which is enough to show that the view opened.
  fetchMock = vi.fn<typeof fetch>(async () =>
    Response.json({ detail: { code: "artifact_not_available", message: "x" } }, { status: 409 }),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  clearApiKey("forgotten");
  sessionStorage.clear();
});

function renderAt(path: string): ReturnType<typeof createMemoryRouter> {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("JobDetailPage evidence", () => {
  it("opens the evidence view for ?observation", async () => {
    renderAt("/jobs/job-1?observation=0");
    await screen.findByRole("heading", { name: "Evidence for observation 1" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/jobs/job-1/artifacts/extraction",
      expect.anything(),
    );
  });

  it("loads nothing without an observation", async () => {
    renderAt("/jobs/job-1");
    await screen.findByRole("heading", { name: "Job" });
    expect(screen.queryByRole("heading", { name: /Evidence/ })).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("ignores an observation that is not a number", async () => {
    renderAt("/jobs/job-1?observation=abc");
    await screen.findByRole("heading", { name: "Job" });
    expect(screen.queryByRole("heading", { name: /Evidence/ })).toBeNull();
  });

  it("removes ?observation on close", async () => {
    const router = renderAt("/jobs/job-1?observation=0&other=1");
    fireEvent.click(await screen.findByRole("button", { name: "Close" }));
    expect(screen.queryByRole("heading", { name: /Evidence/ })).toBeNull();
    expect(router.state.location.search).toBe("?other=1");
  });
});

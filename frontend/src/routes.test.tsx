import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { changeKeyInAnotherTab } from "./api/anotherTab";
import { clearApiKey, setApiKey } from "./api/apiKey";
import { getJob } from "./api/client";
import { routes } from "./routes";

// Vitest globals are off, so Testing Library cannot register its cleanup by itself.
afterEach(cleanup);

afterEach(() => {
  vi.unstubAllGlobals();
  clearApiKey("forgotten");
  sessionStorage.clear();
  localStorage.clear();
});

function renderAt(path: string): ReturnType<typeof createMemoryRouter> {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("routes", () => {
  // With a key, the pages show; the gate is tested below.
  beforeEach(() => {
    setApiKey("test-key", false);
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async (url) =>
        Response.json(url === "/ready" ? { status: "ready" } : []),
      ),
    );
  });

  it("links to every screen from the navigation", async () => {
    renderAt("/");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    const hrefs = within(nav)
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));
    expect(hrefs).toEqual(["/", "/jobs/new", "/key"]);
  });

  it("marks only the current page in the navigation", async () => {
    renderAt("/jobs/new");
    const startJob = await screen.findByRole("link", { name: "Start a job" });
    expect(startJob.getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Dashboard" }).getAttribute("aria-current")).toBeNull();
  });

  it("shows the job id from the path on the job detail page", async () => {
    renderAt("/jobs/abc");
    await screen.findByRole("heading", { name: "Job" });
    expect(screen.getByText("abc")).toBeDefined();
  });

  it("shows a not found page for an unknown path", async () => {
    renderAt("/nope");
    await screen.findByRole("heading", { name: "Page not found" });
  });
});

describe("API key gate", () => {
  it("shows the key screen instead of the page without a key", async () => {
    renderAt("/jobs/abc");
    await screen.findByRole("heading", { name: "API key" });
    expect(screen.queryByRole("heading", { name: "Job" })).toBeNull();
  });

  it("shows the requested page once a key is set, at the same path", async () => {
    const router = renderAt("/jobs/abc");
    await screen.findByRole("heading", { name: "API key" });

    act(() => {
      setApiKey("test-key", false);
    });

    await screen.findByRole("heading", { name: "Job" });
    expect(screen.getByText("abc")).toBeDefined();
    expect(router.state.location.pathname).toBe("/jobs/abc");
  });

  it("shows the key screen again when a request gets a 401", async () => {
    setApiKey("wrong-key", false);
    renderAt("/jobs/abc");
    await screen.findByRole("heading", { name: "Job" });

    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () => new Response(null, { status: 401 })),
    );
    await act(async () => {
      await getJob("abc").catch(() => undefined);
    });

    await screen.findByRole("heading", { name: "API key" });
    expect(screen.queryByRole("heading", { name: "Job" })).toBeNull();
  });

  it("shows the key screen when another tab removes the key", async () => {
    setApiKey("shared-key", true);
    renderAt("/jobs/abc");
    await screen.findByRole("heading", { name: "Job" });

    act(() => {
      changeKeyInAnotherTab(null);
    });

    await screen.findByRole("heading", { name: "API key" });
    expect(screen.getByRole("status").textContent).toBe("The key was removed in another tab.");
  });

  it("stays on the key screen when a request without a key gets a 401", async () => {
    setApiKey("shared-key", true);
    renderAt("/jobs/abc");
    await screen.findByRole("heading", { name: "Job" });
    act(() => {
      changeKeyInAnotherTab(null);
    });
    await screen.findByRole("heading", { name: "API key" });

    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () => new Response(null, { status: 401 })),
    );
    await act(async () => {
      await getJob("abc").catch(() => undefined);
    });

    expect(screen.getByRole("heading", { name: "API key" })).toBeDefined();
    expect(screen.queryByRole("heading", { name: "Job" })).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("The key was removed in another tab.");
  });

  it("shows the requested page when another tab saves a key", async () => {
    renderAt("/jobs/abc");
    await screen.findByRole("heading", { name: "API key" });

    act(() => {
      changeKeyInAnotherTab("shared-key");
    });

    await screen.findByRole("heading", { name: "Job" });
  });
});

import { cleanup, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { routes } from "./routes";

// Vitest globals are off, so Testing Library cannot register its cleanup by itself.
afterEach(cleanup);

function renderAt(path: string): void {
  render(<RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />);
}

describe("routes", () => {
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

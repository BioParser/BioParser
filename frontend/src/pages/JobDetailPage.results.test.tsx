import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, type Mock, vi } from "vitest";
import type { EtsRow } from "../api/client";
import { saveFile } from "../results/saveFile";
import { JobDetailPage } from "./JobDetailPage";

// The browser download is replaced, so a test reads the file the page built.
vi.mock("../results/saveFile", () => ({ saveFile: vi.fn() }));

const JOB_ID = "b3f1c2a4-8d7e-4a65-9e31-123456789abc";
const job = {
  job_id: JOB_ID,
  status: "done",
  display_name: "study.pdf",
  parser: "mineru",
  extractor: "default",
  created_at: "2026-11-10T10:00:00Z",
  document_checksum: "a".repeat(64),
  has_parse_result: true,
};

function etsRow(overrides: Partial<EtsRow> = {}): EtsRow {
  return {
    verbatimTraitName: "body mass",
    scientificName: "Mus musculus",
    verbatimScientificName: "M. musculus",
    verbatimTraitValue: "21.5",
    verbatimTraitUnit: "g",
    measurementID: "1",
    warnings: "",
    references: "a".repeat(64),
    ...overrides,
  };
}

let fetchMock: Mock<typeof fetch>;

// The job read, the results, and the evidence artifacts (409 is enough to open the view).
function stubApi(ets: () => Response) {
  fetchMock = vi.fn<typeof fetch>(async (input) => {
    const path = String(input);
    if (path.endsWith("/ets")) return ets();
    if (path.includes("/artifacts/")) {
      return Response.json(
        { detail: { code: "artifact_not_available", message: "x" } },
        { status: 409 },
      );
    }
    return Response.json(job);
  });
  vi.stubGlobal("fetch", fetchMock);
}

function stubRows(rows: EtsRow[]) {
  stubApi(() => Response.json({ rows }));
}

function renderPage(path = `/jobs/${JOB_ID}`) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([{ path: "/jobs/:jobId", element: <JobDetailPage /> }], {
    initialEntries: [path],
  });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router, client };
}

async function bodyRows(): Promise<HTMLElement[]> {
  const table = await screen.findByRole("table");
  return within(table).getAllByRole("row").slice(1);
}

async function savedText(call: number): Promise<{ name: string; type: string; text: string }> {
  const [name, blob] = vi.mocked(saveFile).mock.calls[call];
  const bytes = new Uint8Array(await blob.arrayBuffer());
  // fatal: invalid UTF-8 throws instead of turning into replacement characters.
  return { name, type: blob.type, text: new TextDecoder("utf-8", { fatal: true }).decode(bytes) };
}

afterEach(() => {
  cleanup();
  vi.mocked(saveFile).mockClear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("JobDetailPage results", () => {
  it("shows one row per ETS row with taxon, trait, reported value and flag", async () => {
    stubRows([
      etsRow(),
      etsRow({ measurementID: "2", scientificName: null, verbatimScientificName: "Sorex sp." }),
    ]);
    renderPage();
    const rows = await bodyRows();
    expect(rows).toHaveLength(2);
    const cells = within(rows[0])
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
    expect(cells).toEqual(["1", "Mus musculus", "body mass", "21.5 g", "None"]);
    // No scientificName: the printed name is shown.
    expect(within(rows[1]).getByText("Sorex sp.")).toBeDefined();
    expect(fetchMock).toHaveBeenCalledWith(`/api/jobs/${JOB_ID}/ets`, expect.anything());
  });

  it("shows the printed trait and value, not a trait list's standardized ones", async () => {
    stubRows([
      etsRow({ traitID: "T1", traitName: "Body mass", traitValue: "0.0215", traitUnit: "kg" }),
    ]);
    renderPage();
    const [row] = await bodyRows();
    expect(within(row).getByText("body mass")).toBeDefined();
    expect(within(row).getByText("21.5 g")).toBeDefined();
    expect(within(row).queryByText("Body mass")).toBeNull();
    expect(within(row).queryByText("0.0215 kg")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Normalized value" })).toBeNull();
  });

  it("flags a row with one warning and explains the code", async () => {
    stubRows([etsRow({ warnings: "not_numeric:age" })]);
    renderPage();
    const [row] = await bodyRows();
    const summary = within(row).getByText("1 warning").closest("summary");
    expect(summary).not.toBeNull();
    const items = within(row).getAllByRole("listitem");
    expect(items).toHaveLength(1);
    expect(items[0].textContent).toBe(
      "not_numeric:age — The age is not a number. The printed text is kept.",
    );
    expect(screen.getByText("1 row of 1 flagged.")).toBeDefined();
  });

  it("lists an unverified name without flagging the row", async () => {
    stubRows([
      etsRow({ warnings: "not_verified:scientificName" }),
      etsRow({ measurementID: "2", warnings: "not_numeric:age" }),
    ]);
    renderPage();
    const [row] = await bodyRows();
    const summary = within(row).getByText("Name not verified");
    expect(summary.tagName).toBe("SUMMARY");
    expect(within(row).queryByText("⚑", { exact: false })).toBeNull();
    expect(within(row).getAllByRole("listitem")[0].textContent).toBe(
      "not_verified:scientificName — The scientific name is the model's reading. No taxonomy lookup has confirmed it.",
    );
    expect(screen.getByText("1 row of 2 flagged.")).toBeDefined();

    fireEvent.click(screen.getByRole("checkbox", { name: "Hide flagged rows" }));
    const visible = await bodyRows();
    expect(visible).toHaveLength(1);
    expect(within(visible[0]).getByRole("button").textContent).toBe("1");
  });

  it("flags a row for a code the UI does not know", async () => {
    stubRows([etsRow({ warnings: "not_verified:scientificName | new_kind:age" })]);
    renderPage();
    const [row] = await bodyRows();
    expect(within(row).getByText("2 warnings")).toBeDefined();
    expect(screen.getByText("1 row of 1 flagged.")).toBeDefined();
  });

  it("lists several warnings, and an unknown code as it is", async () => {
    stubRows([
      etsRow({
        warnings: "not_verified:scientificName | not_a_date:year,month,day | unmapped:traitName",
      }),
    ]);
    renderPage();
    const [row] = await bodyRows();
    expect(within(row).getByText("3 warnings")).toBeDefined();
    const items = within(row)
      .getAllByRole("listitem")
      .map((item) => item.textContent);
    expect(items[1]).toBe(
      "not_a_date:year,month,day — The year, month, and day together are not a calendar day.",
    );
    expect(items[2]).toBe("unmapped:traitName");
  });

  it("hides flagged rows, says how many, and still downloads every row", async () => {
    stubRows([
      etsRow({ warnings: "not_numeric:age" }),
      etsRow({ measurementID: "2" }),
      etsRow({ measurementID: "3", warnings: "out_of_range:month" }),
    ]);
    renderPage();
    expect(await bodyRows()).toHaveLength(3);
    expect(screen.getByText("2 rows of 3 flagged.")).toBeDefined();

    fireEvent.click(screen.getByRole("checkbox", { name: "Hide flagged rows" }));
    const visible = await bodyRows();
    expect(visible).toHaveLength(1);
    // The row keeps its observation number.
    expect(within(visible[0]).getByRole("button").textContent).toBe("2");
    expect(screen.getByText("2 rows hidden. Showing 1 of 3.").getAttribute("role")).toBe("status");

    fireEvent.click(screen.getByRole("button", { name: "Download CSV" }));
    const { text } = await savedText(0);
    expect(text.split("\r\n").filter((line) => line !== "")).toHaveLength(4);
  });

  it("says when every row is hidden", async () => {
    stubRows([etsRow({ warnings: "not_numeric:age" })]);
    renderPage();
    await bodyRows();
    fireEvent.click(screen.getByRole("checkbox", { name: "Hide flagged rows" }));
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByText(/Every observation is flagged/)).toBeDefined();
  });

  it("says that no observations were found", async () => {
    stubRows([]);
    renderPage();
    expect(await screen.findByText("No observations were found in this document.")).toBeDefined();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("opens a row's evidence from its button and from a click on the row", async () => {
    stubRows([etsRow(), etsRow({ measurementID: "2", warnings: "not_numeric:age" })]);
    const { router } = renderPage();
    const rows = await bodyRows();

    const button = within(rows[1]).getByRole("button", {
      name: "Show evidence for observation 2",
    });
    fireEvent.click(button);
    expect(router.state.location.search).toBe("?observation=1");
    expect(
      await screen.findByRole("heading", { name: "Evidence for observation 2" }),
    ).toBeDefined();
    const selected = (await bodyRows())[1];
    expect(selected.getAttribute("aria-current")).toBe("true");

    fireEvent.click(within((await bodyRows())[0]).getByText("Mus musculus"));
    expect(router.state.location.search).toBe("?observation=0");
  });

  it("does not open evidence when the warnings toggle is clicked", async () => {
    stubRows([etsRow({ warnings: "not_numeric:age" })]);
    const { router } = renderPage();
    const [row] = await bodyRows();
    fireEvent.click(within(row).getByText("1 warning"));
    expect(router.state.location.search).toBe("");
  });

  it("downloads the CSV in API column order, UTF-8, with RFC 4180 quoting", async () => {
    stubRows([
      etsRow({
        verbatimScientificName: 'Sorex "araneus", juv.',
        measurementRemarks: "line 1\nline 2",
        verbatimLocality: "Åland",
        warnings: "not_a_date:year,month,day | not_numeric:age",
        aggregateMeasure: true,
      }),
    ]);
    renderPage();
    await bodyRows();
    fireEvent.click(screen.getByRole("button", { name: "Download CSV" }));
    const { name, type, text } = await savedText(0);
    expect(name).toBe(`study-${JOB_ID}.csv`);
    expect(type).toBe("text/csv;charset=utf-8");
    const header = text.slice(0, text.indexOf("\r\n")).split(",");
    expect(header.slice(0, 5)).toEqual([
      "traitID",
      "traitName",
      "verbatimTraitName",
      "scientificName",
      "verbatimScientificName",
    ]);
    expect(text).toContain('"Sorex ""araneus"", juv."');
    expect(text).toContain('"line 1\nline 2"');
    expect(text).toContain("Åland");
    expect(text).toContain('"not_a_date:year,month,day | not_numeric:age"');
    expect(text).toContain(",true,");
    expect(text.endsWith("\r\n")).toBe(true);
  });

  it("downloads the JSON body as returned", async () => {
    const rows = [etsRow({ warnings: "not_numeric:age" }), etsRow({ measurementID: "2" })];
    stubRows(rows);
    renderPage();
    await bodyRows();
    fireEvent.click(screen.getByRole("button", { name: "Download JSON" }));
    const { name, type, text } = await savedText(0);
    expect(name).toBe(`study-${JOB_ID}.json`);
    expect(type).toBe("application/json");
    expect(JSON.parse(text)).toEqual({ rows });
  });

  it("says the results cannot be read on artifact_invalid, and offers no downloads", async () => {
    stubApi(() =>
      Response.json(
        { detail: { code: "artifact_invalid", message: "Job artifact is missing or invalid" } },
        { status: 500 },
      ),
    );
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("The results of this job cannot be read.");
    expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    // artifact_invalid is final: one request, no retries.
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/ets"))).toHaveLength(1);
  });

  it("retries a 503, then offers to try again, and recovers", async () => {
    vi.useFakeTimers();
    let available = false;
    stubApi(() =>
      available
        ? Response.json({ rows: [etsRow()] })
        : Response.json(
            { detail: { code: "storage_unavailable", message: "Artifact storage unavailable" } },
            { status: 503 },
          ),
    );
    renderPage();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(screen.getByText("The results are temporarily unavailable. Retrying…")).toBeDefined();
    // React Query waits 1 s, 2 s, then 4 s between its three retries.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });
    expect(screen.getByRole("alert").textContent).toContain(
      "The results are temporarily unavailable. Try again in a moment.",
    );
    expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();

    available = true;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(screen.getByRole("table")).toBeDefined();
  });
});

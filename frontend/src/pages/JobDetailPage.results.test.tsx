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

// The field list under an expanded row: [label, value text] for each field.
function fieldList(observationNumber: number): HTMLElement | null {
  return document.querySelector(`dl[aria-label="Fields of observation ${observationNumber}"]`);
}

function listedFields(observationNumber = 1): [string, string][] {
  const list = fieldList(observationNumber);
  if (list === null) throw new Error(`observation ${observationNumber} is not expanded`);
  return [...list.querySelectorAll(".results-field")].map((field) => [
    field.querySelector("dt")?.textContent ?? "",
    field.querySelector("dd")?.textContent ?? "",
  ]);
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
    expect(cells).toEqual(["1", "Mus musculus", "body mass", "21.5 g", "None", "▸ 7 fields"]);
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
    expect(within(visible[0]).getByRole("button", { name: /^Show evidence/ }).textContent).toBe(
      "1",
    );
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
    expect(within(visible[0]).getByRole("button", { name: /^Show evidence/ }).textContent).toBe(
      "2",
    );
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

  it("lists a row's filled fields in ETS column order when it is expanded", async () => {
    stubRows([
      etsRow({
        traitID: "T1",
        lifeStage: "adult",
        sex: "",
        individualCount: "   ",
        aggregateMeasure: false,
        measurementRemarks: "line 1\nline 2",
        verbatimLocality: "Åland",
        warnings: "not_numeric:age",
      }),
    ]);
    renderPage();
    const [row] = await bodyRows();
    const toggle = within(row).getByRole("button", { name: "13 fields of observation 1" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(fieldList(1)).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const rows = await bodyRows();
    expect(rows).toHaveLength(2);
    expect(toggle.getAttribute("aria-controls")).toBe(rows[1].id);
    expect(listedFields()).toEqual([
      ["Trait ID", "T1"],
      ["Trait as printed", "body mass"],
      ["Scientific name", "Mus musculus"],
      ["Name as printed", "M. musculus"],
      ["Value as printed", "21.5"],
      ["Unit as printed", "g"],
      ["Life stage", "adult"],
      // age is empty, but its warning gives it a place.
      ["Age", "Empty⚑ The age is not a number. The printed text is kept."],
      ["Measurement remarks", "line 1\nline 2"],
      ["Aggregate measure", "No"],
      ["Measurement ID", "1"],
      ["References", "a".repeat(64)],
      ["Locality as printed", "Åland"],
    ]);
    // Empty and blank columns, and the warnings column itself, are not listed.
    expect(within(rows[1]).queryByText("Sex")).toBeNull();
    expect(within(rows[1]).queryByText("Individual count")).toBeNull();
    expect(within(rows[1]).queryByText("Warnings")).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(await bodyRows()).toHaveLength(1);
  });

  it("marks each field that a warning names next to its value", async () => {
    stubRows([
      etsRow({
        age: "about 3",
        year: "2001",
        month: "13",
        day: "40",
        warnings:
          "not_verified:scientificName | not_numeric:age | not_a_date:year,month,day | " +
          "not_in_vocabulary:basisOfRecord | new_kind:habitat",
      }),
    ]);
    renderPage();
    const [row] = await bodyRows();
    fireEvent.click(within(row).getByRole("button", { name: /fields of observation 1/ }));
    const fields = new Map(listedFields());
    expect(fields.get("Scientific name")).toBe("Mus musculusName not verified");
    expect(fields.get("Age")).toBe("about 3⚑ The age is not a number. The printed text is kept.");
    // One code that names three columns marks each of them.
    for (const label of ["Year", "Month", "Day"]) {
      expect(fields.get(label)).toContain(
        "⚑ The year, month, and day together are not a calendar day.",
      );
    }
    // The adapter left the column empty, and the warning says why.
    expect(fields.get("Basis of record")).toBe(
      "Empty⚑ The basis of record is not one of the accepted values. The column is left empty.",
    );
    // A kind the UI does not know is shown as its code.
    expect(fields.get("Habitat")).toBe("Empty⚑ new_kind:habitat");
    // The flag sign is not read out: the text says what is wrong.
    const mark = within(fieldList(1) as HTMLElement).getByText("new_kind:habitat").parentElement;
    expect(mark?.querySelector('[aria-hidden="true"]')?.textContent).toBe("⚑ ");
  });

  it("expands a row without opening its evidence, and opens evidence without expanding", async () => {
    stubRows([etsRow(), etsRow({ measurementID: "2" })]);
    const { router } = renderPage();
    const [first] = await bodyRows();
    fireEvent.click(within(first).getByRole("button", { name: /fields of observation 1/ }));
    expect(router.state.location.search).toBe("");
    // A click in the field list does not open the evidence either.
    fireEvent.click(screen.getByText("Name as printed"));
    expect(router.state.location.search).toBe("");

    const second = (await bodyRows())[2];
    fireEvent.click(
      within(second).getByRole("button", { name: "Show evidence for observation 2" }),
    );
    expect(router.state.location.search).toBe("?observation=1");
    expect(
      await screen.findByRole("heading", { name: "Evidence for observation 2" }),
    ).toBeDefined();
    // The first row stays expanded beside the evidence, and the second was not expanded.
    const rows = await bodyRows();
    expect(rows).toHaveLength(3);
    expect(
      within(rows[2])
        .getByRole("button", { name: /fields of observation 2/ })
        .getAttribute("aria-expanded"),
    ).toBe("false");
  });

  it("keeps a row expanded when flagged rows are hidden and shown again", async () => {
    stubRows([etsRow({ warnings: "not_numeric:age" }), etsRow({ measurementID: "2" })]);
    renderPage();
    const [flagged] = await bodyRows();
    fireEvent.click(within(flagged).getByRole("button", { name: /fields of observation 1/ }));
    const hide = screen.getByRole("checkbox", { name: "Hide flagged rows" });
    fireEvent.click(hide);
    expect(await bodyRows()).toHaveLength(1);
    fireEvent.click(hide);
    const rows = await bodyRows();
    expect(rows).toHaveLength(3);
    expect(rows[1].classList.contains("results-fields-row")).toBe(true);
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

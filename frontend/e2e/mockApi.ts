import { readFileSync } from "node:fs";
import type { Page } from "@playwright/test";

// A mocked API for the browser tests: three jobs (done, queued, failed), the done job's PDF,
// artifacts and ETS rows, and an API key in the tab.
export const SUM = "a".repeat(64);
export const DONE = "11111111-1111-4111-8111-111111111111";
export const QUEUED = "22222222-2222-4222-8222-222222222222";
export const FAILED = "33333333-3333-4333-8333-333333333333";
export const LONG_NAME =
  "a-very-long-file-name-without-spaces-from-a-journal-supplement-2021-s3.pdf";
const PDF = readFileSync(
  new URL("../../tests/parser/fixtures/plos-biology-3000248.pdf", import.meta.url),
);

function job(id: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    job_id: id,
    status,
    display_name: LONG_NAME,
    parser: "mineru",
    extractor: "default",
    created_at: "2026-11-22T08:48:47Z",
    document_checksum: SUM,
    ...extra,
  };
}

const JOBS = [
  job(QUEUED, "queued"),
  job(DONE, "done"),
  job(FAILED, "failed", { failed_stage: "parsing", error: { code: "timeout" } }),
];

const TEXT = "Adult Apodemus sylvaticus weighed 21.5 g on average in the autumn samples.";
const PARSER = {
  schema_version: "2",
  checksum: SUM,
  parser: { name: "default", version: "2", configuration: {} },
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
          content: { type: "text", text: TEXT, lines: [] },
          bbox: { x0: 0.1, y0: 0.2, x1: 0.9, y1: 0.26 },
        },
      ],
    },
  ],
};

function citation(text: string) {
  const start = TEXT.indexOf(text);
  return {
    kind: "citation",
    text,
    evidence: [{ block_id: "b0", start, end: start + text.length }],
  };
}

const EXTRACTION = {
  schema_version: "2",
  extractor: "default",
  extractor_version: "2",
  observations: [
    {
      traitCode: { kind: "interpretation", value: "body_mass", passage: citation("21.5 g") },
      verbatimTraitName: citation("weighed"),
      verbatimScientificName: citation("Apodemus sylvaticus"),
      verbatimTraitValue: citation("21.5"),
      verbatimTraitUnit: citation("g"),
    },
  ],
};

export const ROWS = {
  rows: [
    {
      measurementID: "1",
      scientificName: "Apodemus sylvaticus",
      verbatimScientificName: "A. sylvaticus",
      verbatimTraitName: "body mass",
      verbatimTraitValue: "21.5",
      verbatimTraitUnit: "g",
      warnings: "not_verified:scientificName | not_numeric:measurementValue_min",
      // A long value with no spaces, to check that the expanded field list wraps it.
      measurementRemarks: `Measured-at-https://example.org/${"very-long-path-".repeat(6)}`,
      references: SUM,
    },
  ],
};

// Answers every API call the UI makes, from the data above. `count` repeats the one
// observation, in the ETS rows and in the extraction artifact, for a long results table.
export async function mockApi(page: Page, { withKey = true, count = 1 } = {}) {
  const rows = Array.from({ length: count }, (_, i) => ({
    ...ROWS.rows[0],
    measurementID: String(i + 1),
  }));
  const extraction = {
    ...EXTRACTION,
    observations: Array.from({ length: count }, () => EXTRACTION.observations[0]),
  };
  if (withKey) {
    await page.addInitScript(() => sessionStorage.setItem("bioparser.apiKey", "test-key"));
  }
  await page.route("**/ready", (route) => route.fulfill({ json: { status: "ready" } }));
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    const id = path.split("/")[3] ?? "";
    if (path === "/api/jobs") return route.fulfill({ json: JOBS });
    if (path === "/api/upload-limits")
      return route.fulfill({ json: { max_upload_bytes: 20971520 } });
    if (path.endsWith("/ets")) return route.fulfill({ json: { rows } });
    if (path.endsWith("/artifacts/parser")) return route.fulfill({ json: PARSER });
    if (path.endsWith("/artifacts/extraction")) return route.fulfill({ json: extraction });
    if (path.endsWith("/file")) {
      return route.fulfill({ body: PDF, contentType: "application/pdf" });
    }
    const found = JOBS.find((item) => item.job_id === id);
    if (path.startsWith("/api/jobs/") && found) return route.fulfill({ json: found });
    return route.fulfill({
      status: 404,
      json: { detail: { code: "not_found", message: "Not found" } },
    });
  });
}

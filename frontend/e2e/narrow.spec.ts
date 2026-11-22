import { readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";

// Every page and view at 360 px, the narrowest phone width #323 asks for. A page fails when
// it scrolls sideways: a wide table, a long file name or a grid that does not wrap.
test.use({ viewport: { width: 360, height: 740 } });

const SUM = "a".repeat(64);
const DONE = "11111111-1111-4111-8111-111111111111";
const QUEUED = "22222222-2222-4222-8222-222222222222";
const FAILED = "33333333-3333-4333-8333-333333333333";
const LONG_NAME = "a-very-long-file-name-without-spaces-from-a-journal-supplement-2021-s3.pdf";
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
    has_parse_result: status !== "queued",
    ...extra,
  };
}

const JOBS = [
  job(QUEUED, "queued"),
  job(DONE, "done"),
  job(FAILED, "failed", { has_parse_result: false, error: { code: "timeout" } }),
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

const ROWS = {
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

// Answers every API call the UI makes, from the data above.
async function mockApi(page: Page, { withKey = true } = {}) {
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
    if (path.endsWith("/ets")) return route.fulfill({ json: ROWS });
    if (path.endsWith("/artifacts/parser")) return route.fulfill({ json: PARSER });
    if (path.endsWith("/artifacts/extraction")) return route.fulfill({ json: EXTRACTION });
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

async function expectNoSidewaysScroll(page: Page) {
  await page.evaluate(() => document.fonts.ready);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, "the page scrolls sideways at 360 px").toBe(0);
}

test("the API key screen", async ({ page }) => {
  await mockApi(page, { withKey: false });
  await page.goto("/ui/");
  await expect(page.getByLabel("API key")).toBeVisible();
  await expectNoSidewaysScroll(page);
});

test("the API key page with a stored key", async ({ page }) => {
  await mockApi(page);
  await page.goto("/ui/key");
  await expect(page.getByRole("button", { name: "Forget key" })).toBeVisible();
  await expectNoSidewaysScroll(page);
});

test("the dashboard", async ({ page }) => {
  await mockApi(page);
  await page.goto("/ui/");
  await expect(page.getByRole("table")).toBeVisible();
  await expect(page.getByText("Up").first()).toBeVisible();
  await expectNoSidewaysScroll(page);
});

test("Start a job with files in the list", async ({ page }) => {
  await mockApi(page);
  await page.goto("/ui/jobs/new");
  const pdf = (name: string) => ({
    name,
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.7\n"),
  });
  await page.getByLabel("Add PDF files").setInputFiles([pdf(LONG_NAME), pdf("b.pdf")]);
  await expect(page.getByRole("list", { name: "Files" })).toBeVisible();
  await expectNoSidewaysScroll(page);
});

test("a queued job", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/ui/jobs/${QUEUED}`);
  await expect(page.getByText("Status: queued")).toBeVisible();
  await expectNoSidewaysScroll(page);
});

test("a failed job", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/ui/jobs/${FAILED}`);
  await expect(page.getByText("Job failed:")).toBeVisible();
  await expectNoSidewaysScroll(page);
});

test("a done job's results", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/ui/jobs/${DONE}`);
  await expect(page.getByRole("region", { name: "Observations" })).toBeVisible();
  // The row's fields open in a row below it (#402), which must not widen the page either.
  await page.getByRole("button", { name: /fields of observation 1$/ }).click();
  await expect(page.locator(".results-fields")).toBeVisible();
  // The list fits the table's scroll box, so it reads without scrolling the table sideways.
  const room = await page.evaluate(() => {
    const box = document.querySelector(".results-scroll");
    const list = document.querySelector(".results-fields");
    return box && list ? box.clientWidth - list.getBoundingClientRect().width : -1;
  });
  expect(room, "the field list is wider than the table's box").toBeGreaterThanOrEqual(0);
  await expectNoSidewaysScroll(page);
});

test("the question before a job is deleted", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/ui/jobs/${DONE}`);
  await page.getByRole("button", { name: "Delete job" }).click();
  await expect(page.getByText(/Delete this job\?/)).toBeVisible();
  await expectNoSidewaysScroll(page);
});

test("the evidence view with the PDF page", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/ui/jobs/${DONE}?observation=0`);
  await expect(page.locator('.pdf-page-frame[data-state="drawn"]')).toBeVisible();
  await expectNoSidewaysScroll(page);
});

test("the not-found page", async ({ page }) => {
  await mockApi(page);
  await page.goto("/ui/no-such-page");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  await expectNoSidewaysScroll(page);
});

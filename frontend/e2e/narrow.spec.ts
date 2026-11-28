import { expect, type Page, test } from "@playwright/test";
import { DONE, FAILED, LONG_NAME, mockApi, QUEUED } from "./mockApi.ts";

// Every page and view at 360 px, the narrowest phone width #323 asks for. A page fails when
// it scrolls sideways: a wide table, a long file name or a grid that does not wrap.
test.use({ viewport: { width: 360, height: 740 } });

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

test("editing a document title", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/ui/jobs/${QUEUED}`);
  await page.getByRole("button", { name: "Edit title" }).click();
  const input = page.getByRole("textbox", { name: "Title", exact: true });
  await expect(input).toBeFocused();
  const title = "A-long-document-title-".repeat(12);
  await input.fill(title);
  await expectNoSidewaysScroll(page);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit title" })).toBeFocused();
  await expectNoSidewaysScroll(page);
  await page.getByRole("button", { name: "Edit title" }).click();
  await input.fill("");
  await input.press("Enter");
  await expect(page.getByRole("heading", { name: LONG_NAME, exact: true })).toBeVisible();
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

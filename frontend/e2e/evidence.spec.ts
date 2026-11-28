import { expect, test } from "@playwright/test";
import { DONE, mockApi } from "./mockApi.ts";

// On a wide screen the evidence view opens beside the results table. Selecting a row must
// leave the page where it is: a long table puts the row far below the top of the page.
test.use({ viewport: { width: 1280, height: 800 } });

test("selecting a row keeps the page where it is", async ({ page }) => {
  await mockApi(page, { count: 30 });
  await page.goto(`/ui/jobs/${DONE}`);
  const rows = page.getByRole("region", { name: "Observations" });
  await expect(rows).toBeVisible();

  // The first selection opens the view, the second changes the observation in it. The row
  // stays where it was on screen. The page may move a little: when the view opens, the table
  // gets narrower and its rows taller, and the browser keeps the selected row in place.
  for (const number of ["25", "28"]) {
    const row = page.getByRole("button", { name: `Show evidence for observation ${number}` });
    await row.scrollIntoViewIfNeeded();
    expect(
      await page.evaluate(() => window.scrollY),
      "the row is far down the page",
    ).toBeGreaterThan(800);
    const before = (await row.boundingBox())?.y ?? 0;
    await row.click();
    await expect(page.locator('.pdf-page-frame[data-state="drawn"]')).toBeVisible();
    await expect(
      page.getByRole("heading", { name: `Evidence for observation ${number}` }),
    ).toBeVisible();
    const after = (await row.boundingBox())?.y ?? 0;
    expect(Math.abs(after - before), "the selected row moved on screen").toBeLessThan(50);
    await expect(row).toBeInViewport();
    // The evidence is on screen beside the row.
    await expect(page.locator("mark.evidence-value").first()).toBeInViewport();
  }
});

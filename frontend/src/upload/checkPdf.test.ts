import { describe, expect, it } from "vitest";
import { checkPdf, EMPTY, MAX_UPLOAD_BYTES, NO_PDF_HEADER, NOT_PDF, TOO_LARGE } from "./checkPdf";

function pdf(content: BlobPart, type = "application/pdf"): File {
  return new File([content], "paper.pdf", { type });
}

describe("checkPdf", () => {
  it("accepts a PDF", async () => {
    await expect(checkPdf(pdf("%PDF-1.7\n"))).resolves.toBeNull();
  });

  it("refuses a file that is not a PDF", async () => {
    await expect(checkPdf(pdf("%PDF-1.7\n", "text/plain"))).resolves.toBe(NOT_PDF);
  });

  it("refuses a file without a type", async () => {
    await expect(checkPdf(pdf("%PDF-1.7\n", ""))).resolves.toBe(NOT_PDF);
  });

  it("refuses an empty file", async () => {
    await expect(checkPdf(pdf(""))).resolves.toBe(EMPTY);
  });

  it("accepts a file of exactly 20 MiB", async () => {
    const content = new Uint8Array(MAX_UPLOAD_BYTES);
    content.set(new TextEncoder().encode("%PDF-"));
    await expect(checkPdf(pdf(content))).resolves.toBeNull();
  });

  it("refuses a file over 20 MiB", async () => {
    await expect(checkPdf(pdf(new Uint8Array(MAX_UPLOAD_BYTES + 1)))).resolves.toBe(TOO_LARGE);
  });

  it("refuses a file that does not start with %PDF-", async () => {
    await expect(checkPdf(pdf("<html>%PDF-1.7"))).resolves.toBe(NO_PDF_HEADER);
  });

  it("refuses a file shorter than %PDF-", async () => {
    await expect(checkPdf(pdf("%PD"))).resolves.toBe(NO_PDF_HEADER);
  });
});

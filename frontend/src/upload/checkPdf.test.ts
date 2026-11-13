import { describe, expect, it } from "vitest";
import {
  checkPdf,
  DEFAULT_MAX_UPLOAD_BYTES,
  EMPTY,
  NO_PDF_HEADER,
  NOT_PDF,
  tooLarge,
} from "./checkPdf";

const LIMIT = DEFAULT_MAX_UPLOAD_BYTES;

function pdf(content: BlobPart, type = "application/pdf"): File {
  return new File([content], "paper.pdf", { type });
}

// A PDF that reports the given size without allocating that many bytes.
function pdfOfSize(size: number): File {
  const file = pdf("%PDF-1.7\n");
  Object.defineProperty(file, "size", { value: size });
  return file;
}

describe("checkPdf", () => {
  it("accepts a PDF", async () => {
    await expect(checkPdf(pdf("%PDF-1.7\n"), LIMIT)).resolves.toBeNull();
  });

  it("refuses a file that is not a PDF", async () => {
    await expect(checkPdf(pdf("%PDF-1.7\n", "text/plain"), LIMIT)).resolves.toBe(NOT_PDF);
  });

  it("refuses a file without a type", async () => {
    await expect(checkPdf(pdf("%PDF-1.7\n", ""), LIMIT)).resolves.toBe(NOT_PDF);
  });

  it("refuses an empty file", async () => {
    await expect(checkPdf(pdf(""), LIMIT)).resolves.toBe(EMPTY);
  });

  it("accepts a file of exactly the limit", async () => {
    await expect(checkPdf(pdfOfSize(LIMIT), LIMIT)).resolves.toBeNull();
  });

  it("refuses a file over the limit", async () => {
    await expect(checkPdf(pdfOfSize(LIMIT + 1), LIMIT)).resolves.toBe(tooLarge(LIMIT));
  });

  it("uses the limit it is given", async () => {
    const oneMiB = 1024 * 1024;
    await expect(checkPdf(pdfOfSize(oneMiB + 1), oneMiB)).resolves.toBe("The file is over 1 MiB.");
  });

  it("refuses a file that does not start with %PDF-", async () => {
    await expect(checkPdf(pdf("<html>%PDF-1.7"), LIMIT)).resolves.toBe(NO_PDF_HEADER);
  });

  it("refuses a file shorter than %PDF-", async () => {
    await expect(checkPdf(pdf("%PD"), LIMIT)).resolves.toBe(NO_PDF_HEADER);
  });
});

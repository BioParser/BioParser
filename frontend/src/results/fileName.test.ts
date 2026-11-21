import { describe, expect, it } from "vitest";
import { downloadName } from "./fileName";

const JOB = "b3f1c2a4-8d7e-4a65-9e31-123456789abc";

describe("downloadName", () => {
  it("uses the PDF name without its extension, and the job ID", () => {
    expect(downloadName("study.pdf", JOB, "csv")).toBe(`study-${JOB}.csv`);
    expect(downloadName("Study.PDF", JOB, "json")).toBe(`Study-${JOB}.json`);
  });

  it("keeps non-ASCII letters and spaces", () => {
    expect(downloadName("Hiiret ja myyrät 2024.pdf", JOB, "csv")).toBe(
      `Hiiret ja myyrät 2024-${JOB}.csv`,
    );
  });

  it("removes path separators and control characters", () => {
    expect(downloadName("../../etc/passwd", JOB, "csv")).toBe(`....etcpasswd-${JOB}.csv`);
    expect(downloadName("a\\b\u0000c\nd\u007f\u0085e.pdf", JOB, "csv")).toBe(`abcde-${JOB}.csv`);
  });

  it("uses the job ID alone when there is no usable name", () => {
    expect(downloadName(null, JOB, "csv")).toBe(`${JOB}.csv`);
    expect(downloadName("/\u0001/", JOB, "json")).toBe(`${JOB}.json`);
  });

  it("falls back to 'results' when nothing is left", () => {
    expect(downloadName(null, "", "csv")).toBe("results.csv");
  });
});

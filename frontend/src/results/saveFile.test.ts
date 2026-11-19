import { afterEach, describe, expect, it, vi } from "vitest";
import { saveFile } from "./saveFile";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("saveFile", () => {
  it("clicks a download link for an object URL, then removes the link and revokes the URL", () => {
    vi.useFakeTimers();
    // jsdom has no object URLs.
    const createObjectURL = vi.fn(() => "blob:bioparser/1");
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    let clicked: HTMLAnchorElement | null = null;
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked = this;
    });
    const blob = new Blob(["a,b\r\n"], { type: "text/csv;charset=utf-8" });

    saveFile("study-1.csv", blob);

    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(clicked).not.toBeNull();
    const link = clicked as unknown as HTMLAnchorElement;
    expect(link.download).toBe("study-1.csv");
    expect(link.getAttribute("href")).toBe("blob:bioparser/1");
    expect(link.isConnected).toBe(false);
    expect(revokeObjectURL).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:bioparser/1");
  });
});

import { act, cleanup, render, screen } from "@testing-library/react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { outlineStyle, PdfPage } from "./PdfPage";

// Vitest globals are off, so Testing Library cannot register its cleanup by itself.
afterEach(cleanup);

// jsdom has no layout: this stands in for ResizeObserver and reports the widths a test sets.
let resize: (width: number) => void = () => {};
class FakeResizeObserver {
  private readonly callback: ResizeObserverCallback;
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }
  observe() {
    resize = (width) =>
      this.callback(
        [{ contentRect: { width } } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
  }
  disconnect() {
    resize = () => {};
  }
}

// A draw that finishes only when the test says so, to check what happens in between.
interface Draw {
  scale: number;
  finish: () => void;
  fail: (error: Error) => void;
  cancel: ReturnType<typeof vi.fn>;
}
let draws: Draw[] = [];

// A letter-size page, 612 by 792 points, as pdf.js reports it.
function fakePdf(getPage?: () => Promise<unknown>): PDFDocumentProxy {
  const page = {
    getViewport: ({ scale }: { scale: number }) => ({ width: 612 * scale, height: 792 * scale }),
    render: ({ viewport }: { viewport: { width: number } }) => {
      let finish = () => {};
      let fail: (error: Error) => void = () => {};
      const promise = new Promise<void>((resolve, reject) => {
        finish = resolve;
        fail = reject;
      });
      const cancel = vi.fn(() => fail(new Error("Rendering cancelled")));
      draws.push({ scale: viewport.width / 612, finish, fail, cancel });
      return { promise, cancel };
    },
  };
  return { getPage: vi.fn(getPage ?? (async () => page)) } as unknown as PDFDocumentProxy;
}

const BOX = { x0: 0.1, y0: 0.25, x1: 0.6, y1: 0.3 };

beforeEach(() => {
  draws = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function settle() {
  // Lets the getPage promise and the state updates after it run.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function canvas(): HTMLCanvasElement {
  return screen.getByRole("img", { name: "Page 3 of the PDF" }) as HTMLCanvasElement;
}

describe("outlineStyle", () => {
  it("places a normalized box in percentages of the page", () => {
    expect(outlineStyle(BOX)).toEqual({ left: "10%", top: "25%", width: "50%", height: "5%" });
  });
});

describe("PdfPage", () => {
  it("draws the page at the width of its frame", async () => {
    render(<PdfPage pdf={fakePdf()} pageNumber={3} outlines={[]} />);
    act(() => resize(306));
    await settle();
    expect(draws).toHaveLength(1);
    expect(draws[0]?.scale).toBe(0.5);
    expect([canvas().width, canvas().height]).toEqual([306, 396]);
  });

  it("does not draw before the frame has a width", async () => {
    const pdf = fakePdf();
    render(<PdfPage pdf={pdf} pageNumber={3} outlines={[]} />);
    await settle();
    expect(pdf.getPage).not.toHaveBeenCalled();
  });

  it("outlines the blocks once the page is drawn", async () => {
    const { container } = render(
      <PdfPage pdf={fakePdf()} pageNumber={3} outlines={[{ blockId: "b7", bbox: BOX }]} />,
    );
    act(() => resize(306));
    await settle();
    expect(container.querySelector(".pdf-outline")).toBeNull();
    await act(async () => draws[0]?.finish());
    const outline = container.querySelector<HTMLElement>(".pdf-outline");
    expect(outline?.dataset.blockId).toBe("b7");
    expect(outline?.style.left).toBe("10%");
    expect(outline?.style.top).toBe("25%");
    expect(outline?.style.width).toBe("50%");
    expect(outline?.style.height).toBe("5%");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("redraws at a new width and stops the draw in progress", async () => {
    render(<PdfPage pdf={fakePdf()} pageNumber={3} outlines={[]} />);
    act(() => resize(306));
    await settle();
    act(() => resize(612));
    await settle();
    expect(draws.map((draw) => draw.scale)).toEqual([0.5, 1]);
    expect(draws[0]?.cancel).toHaveBeenCalled();
    await act(async () => draws[1]?.finish());
    expect(canvas().width).toBe(612);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps the outlines while it redraws after a resize", async () => {
    const { container } = render(
      <PdfPage pdf={fakePdf()} pageNumber={3} outlines={[{ blockId: "b7", bbox: BOX }]} />,
    );
    act(() => resize(306));
    await settle();
    await act(async () => draws[0]?.finish());
    act(() => resize(500));
    await settle();
    expect(container.querySelector(".pdf-outline")).not.toBeNull();
  });

  it("stops drawing when it is removed", async () => {
    const { unmount } = render(<PdfPage pdf={fakePdf()} pageNumber={3} outlines={[]} />);
    act(() => resize(306));
    await settle();
    unmount();
    expect(draws[0]?.cancel).toHaveBeenCalled();
  });

  it("says when the page cannot be drawn", async () => {
    render(
      <PdfPage
        pdf={fakePdf(async () => Promise.reject(new Error("Invalid page request.")))}
        pageNumber={3}
        outlines={[{ blockId: "b7", bbox: BOX }]}
      />,
    );
    act(() => resize(306));
    await settle();
    expect(screen.getByRole("alert").textContent).toBe(" could not be drawn.");
    expect(screen.getByText(/^Page 3/).textContent).toBe("Page 3 could not be drawn.");
  });
});

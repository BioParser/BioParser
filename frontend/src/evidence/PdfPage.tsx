import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import { type CSSProperties, type RefObject, useEffect, useRef, useState } from "react";
import type { BoundingBox } from "./quotation";

export interface Outline {
  blockId: string;
  bbox: BoundingBox;
}

interface PdfPageProps {
  pdf: PDFDocumentProxy;
  pageNumber: number;
  outlines: Outline[];
}

// A canvas past twice the CSS size costs memory on a phone without looking sharper.
const MAX_PIXEL_RATIO = 2;

// A box from the parser is a fraction of the page with the origin at the top left, so it
// becomes percentages of the drawn page and stays aligned at any width without redrawing.
export function outlineStyle(bbox: BoundingBox): CSSProperties {
  return {
    left: percent(bbox.x0),
    top: percent(bbox.y0),
    width: percent(bbox.x1 - bbox.x0),
    height: percent(bbox.y1 - bbox.y0),
  };
}

// Rounded so 0.3 - 0.25 reads 5%, not 4.999999999999999%; 0.0001% is far below a pixel.
function percent(fraction: number): string {
  return `${Number((fraction * 100).toFixed(4))}%`;
}

// One page of the PDF at the width of its container, redrawn when that width changes, with
// the given blocks outlined over it. Another page is another component: key it by page.
export function PdfPage({ pdf, pageNumber, outlines }: PdfPageProps) {
  const frameRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const width = useElementWidth(frameRef);
  const [state, setState] = useState<"drawing" | "drawn" | "failed">("drawing");

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null || width === 0) {
      return;
    }
    // Set before a cancel, so a draw this effect stopped is not reported as a failure.
    let stopped = false;
    let task: RenderTask | null = null;
    // A redraw at a new width keeps the outlines: their percentages fit any width.
    (async () => {
      const page = await pdf.getPage(pageNumber);
      if (stopped) {
        return;
      }
      const pixelRatio = Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO);
      const scale = (width / page.getViewport({ scale: 1 }).width) * pixelRatio;
      const viewport = page.getViewport({ scale });
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      task = page.render({ canvas, viewport });
      await task.promise;
      if (!stopped) {
        setState("drawn");
      }
    })().catch(() => {
      if (!stopped) {
        setState("failed");
      }
    });
    return () => {
      stopped = true;
      task?.cancel();
    };
  }, [pdf, pageNumber, width]);

  return (
    <figure className="pdf-page">
      <div ref={frameRef} className="pdf-page-frame" data-state={state}>
        <canvas ref={canvasRef} role="img" aria-label={`Page ${pageNumber} of the PDF`} />
        {state === "drawn" &&
          outlines.map((outline) => (
            <div
              key={outline.blockId}
              className="pdf-outline"
              style={outlineStyle(outline.bbox)}
              data-block-id={outline.blockId}
            />
          ))}
      </div>
      <figcaption>
        Page {pageNumber}
        {state === "drawing" && <span role="status"> (drawing…)</span>}
        {state === "failed" && <span role="alert"> could not be drawn.</span>}
      </figcaption>
    </figure>
  );
}

// The element's width in whole CSS pixels, kept current while it resizes.
function useElementWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (element === null) {
      return;
    }
    const observer = new ResizeObserver(([entry]) => {
      if (entry !== undefined) {
        setWidth(Math.round(entry.contentRect.width));
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

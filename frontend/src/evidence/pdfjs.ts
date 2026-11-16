// pdf.js and its worker, loaded only when a PDF is opened: this module is imported dynamically,
// so the library stays out of the main bundle.
import { GlobalWorkerOptions, getDocument, type PDFDocumentProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

GlobalWorkerOptions.workerSrc = workerUrl;

// getDocument moves the bytes to the worker, so the buffer is empty afterwards.
export function openPdf(bytes: ArrayBuffer): Promise<PDFDocumentProxy> {
  return getDocument({ data: new Uint8Array(bytes) }).promise;
}

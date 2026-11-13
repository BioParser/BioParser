import { ApiError } from "../api/client";
import { type Outline, PdfPage } from "./PdfPage";
import type { QuotedBlock } from "./quotation";
import { usePdfDocument } from "./usePdfDocument";

interface PageOfBlocks {
  pageNumber: number;
  outlines: Outline[];
  // Blocks on this page that the parser gave no box for.
  unplaced: string[];
}

// The quoted blocks by page, in the order the quotation shows them. A value over two blocks
// gives one page with two outlines, or two pages.
export function pagesOf(quotation: QuotedBlock[]): PageOfBlocks[] {
  const pages = new Map<number, PageOfBlocks>();
  for (const block of quotation) {
    let page = pages.get(block.pageNumber);
    if (page === undefined) {
      page = { pageNumber: block.pageNumber, outlines: [], unplaced: [] };
      pages.set(block.pageNumber, page);
    }
    if (block.bbox === null) {
      page.unplaced.push(block.blockId);
    } else {
      page.outlines.push({ blockId: block.blockId, bbox: block.bbox });
    }
  }
  return [...pages.values()];
}

// The PDF pages that hold the quotation, with its blocks outlined. Its errors are its own,
// so the quotation above stays whatever happens to the PDF.
export function EvidencePdf({ jobId, quotation }: { jobId: string; quotation: QuotedBlock[] }) {
  const { checksum, pdf } = usePdfDocument(jobId, true);
  const error = checksum.error ?? pdf.error;
  if (error !== null) {
    return (
      <p role="alert" className="evidence-error">
        {documentError(error, checksum.error !== null)}
      </p>
    );
  }
  if (pdf.data === undefined) {
    return <p role="status">Loading the PDF…</p>;
  }
  return (
    <>
      {pagesOf(quotation).map((page) => (
        <div key={page.pageNumber}>
          <PdfPage pdf={pdf.data} pageNumber={page.pageNumber} outlines={page.outlines} />
          {page.unplaced.map((blockId) => (
            <p key={blockId} className="evidence-note">
              The parser gave no position for block <code>{blockId}</code>, so it is not outlined.
            </p>
          ))}
        </div>
      ))}
    </>
  );
}

export function documentError(error: Error, fromJob: boolean): string {
  if (error instanceof TypeError) {
    return "The PDF could not be loaded. Check the connection and reopen the observation.";
  }
  if (!(error instanceof ApiError)) {
    // pdf.js could not open the bytes the API sent.
    return "The PDF of this job cannot be opened. The file may be damaged.";
  }
  if (fromJob) {
    return error.status === 404 ? "This job does not exist." : error.message;
  }
  switch (error.status) {
    case 404:
      return "The PDF of this job is missing.";
    case 500:
      return "The stored PDF of this job cannot be read.";
    default:
      return error.message;
  }
}

// The API's default upload limit (DEFAULT_MAX_UPLOAD_BYTES in src/bioparser/api/config.py).
// Used only until GET /api/upload-limits answers, or if that call fails.
export const DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

// The first bytes of every PDF (_PDF_MAGIC in src/bioparser/api/uploads.py).
const PDF_MAGIC = "%PDF-";

export const NOT_PDF = "Not a PDF file.";
export const EMPTY = "The file is empty.";
export const NO_PDF_HEADER = "The file does not start with %PDF-, so it is not a PDF.";

// "20 MiB", "1.5 MiB".
export function formatMiB(bytes: number): string {
  return `${Number((bytes / (1024 * 1024)).toFixed(1))} MiB`;
}

export function tooLarge(maxBytes: number): string {
  return `The file is over ${formatMiB(maxBytes)}.`;
}

// The checks the API makes, so a file that would be refused is not sent. (The API checks
// the size before the type; the order does not change what the curator sees.) Returns why
// the file is refused, or null when it can be sent.
export async function checkPdf(file: File, maxBytes: number): Promise<string | null> {
  // The API accepts only application/pdf (validate_content_type), the type the browser
  // gives a file ending in .pdf.
  if (file.type !== "application/pdf") {
    return NOT_PDF;
  }
  if (file.size === 0) {
    return EMPTY;
  }
  if (file.size > maxBytes) {
    return tooLarge(maxBytes);
  }
  // Read only the first bytes, not the whole file.
  const start = await file.slice(0, PDF_MAGIC.length).text();
  return start === PDF_MAGIC ? null : NO_PDF_HEADER;
}

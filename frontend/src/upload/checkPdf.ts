// The API's default upload limit (DEFAULT_MAX_UPLOAD_BYTES in src/bioparser/api/config.py).
// No route returns the limit the API runs with, so the browser assumes the default.
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

// The first bytes of every PDF (_PDF_MAGIC in src/bioparser/api/uploads.py).
const PDF_MAGIC = "%PDF-";

export const NOT_PDF = "Not a PDF file.";
export const EMPTY = "The file is empty.";
export const TOO_LARGE = "The file is over 20 MiB.";
export const NO_PDF_HEADER = "The file does not start with %PDF-, so it is not a PDF.";

// The same checks the API makes, in the same order, so a file that would be refused is not
// sent. Returns why the file is refused, or null when it can be sent.
export async function checkPdf(file: File): Promise<string | null> {
  // The API accepts only application/pdf (validate_content_type), the type the browser
  // gives a file ending in .pdf.
  if (file.type !== "application/pdf") {
    return NOT_PDF;
  }
  if (file.size === 0) {
    return EMPTY;
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return TOO_LARGE;
  }
  // Read only the first bytes, not the whole file.
  const start = await file.slice(0, PDF_MAGIC.length).text();
  return start === PDF_MAGIC ? null : NO_PDF_HEADER;
}

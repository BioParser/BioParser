// A download name from the PDF's display name and the job ID, such as
// "study-3f0c….csv". Path separators and control characters are removed, so the name
// cannot point into a folder or break a header line. The browser cleans it further for the
// platform, for example characters Windows does not allow.
export function downloadName(
  displayName: string | null,
  jobId: string,
  extension: "csv" | "json",
): string {
  const stem = clean(displayName ?? "").replace(/\.pdf$/i, "");
  const id = clean(jobId);
  const base = [stem, id].filter((part) => part !== "").join("-");
  return `${base === "" ? "results" : base}.${extension}`;
}

function clean(text: string): string {
  return Array.from(text)
    .filter((char) => !isUnsafe(char))
    .join("")
    .trim();
}

function isUnsafe(char: string): boolean {
  const code = char.codePointAt(0) ?? 0;
  // C0 controls, DEL, and C1 controls; then the two path separators.
  return code < 0x20 || (code >= 0x7f && code <= 0x9f) || char === "/" || char === "\\";
}

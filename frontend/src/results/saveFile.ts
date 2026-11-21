// Saves a file the browser built. No request is sent: the API routes need X-API-Key, which
// a plain link cannot send, and the rows are already loaded.
export function saveFile(name: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.hidden = true;
  // Firefox starts the download only for a link that is in the document.
  document.body.append(link);
  link.click();
  link.remove();
  // Revoked on the next task: revoking during the click can cancel the download.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

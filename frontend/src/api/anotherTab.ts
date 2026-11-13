// Tests have a single tab. This does what happens in it when another tab saves (newValue) or
// removes (null) the remembered key: localStorage changes, then the browser sends a storage event.
export function changeKeyInAnotherTab(newValue: string | null): void {
  if (newValue === null) {
    localStorage.removeItem("bioparser.apiKey");
  } else {
    localStorage.setItem("bioparser.apiKey", newValue);
  }
  window.dispatchEvent(new StorageEvent("storage", { key: "bioparser.apiKey", newValue }));
}

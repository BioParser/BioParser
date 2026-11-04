// The shared API key the UI sends as X-API-Key (#253).
// sessionStorage keeps it until the tab is closed; localStorage keeps it on this device.
// If the browser blocks storage, the key is kept in memory until the page is reloaded.

const STORAGE_KEY = "bioparser.apiKey";

type StorageName = "sessionStorage" | "localStorage";

// Why the key was last removed: the user forgot it, the API answered 401, or another tab
// removed the remembered key.
export type RemovedReason = "forgotten" | "rejected" | "elsewhere";

let memoryKey: string | null = null;
let removedReason: RemovedReason | null = null;
const listeners = new Set<() => void>();

// When another tab saves or removes a remembered key, the browser sends this tab a storage
// event. The tab that made the change gets none: it already notified its own listeners.
// event.key is null when another tab cleared all of this site's storage.
window.addEventListener("storage", (event) => {
  if (event.key !== STORAGE_KEY && event.key !== null) {
    return;
  }
  removedReason = getApiKey() === null ? "elsewhere" : null;
  notifyListeners();
});

export function getApiKey(): string | null {
  return memoryKey ?? readFrom("sessionStorage") ?? readFrom("localStorage");
}

export function getRemovedReason(): RemovedReason | null {
  return removedReason;
}

export function setApiKey(key: string, remember: boolean): void {
  removeStoredKey();
  if (!writeTo(remember ? "localStorage" : "sessionStorage", key)) {
    memoryKey = key;
  }
  removedReason = null;
  notifyListeners();
}

export function clearApiKey(reason: RemovedReason): void {
  removeStoredKey();
  removedReason = reason;
  notifyListeners();
}

// Calls listener after every setApiKey and clearApiKey. Returns a function that stops the calls.
export function subscribeToApiKey(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function notifyListeners(): void {
  for (const listener of listeners) {
    listener();
  }
}

function removeStoredKey(): void {
  memoryKey = null;
  removeFrom("sessionStorage");
  removeFrom("localStorage");
}

// Each helper catches the error a browser throws when storage is blocked or full.
// Even reading window.localStorage can throw, so that happens inside the try too.

function readFrom(name: StorageName): string | null {
  try {
    return window[name].getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeTo(name: StorageName, key: string): boolean {
  try {
    window[name].setItem(STORAGE_KEY, key);
    return true;
  } catch {
    return false;
  }
}

function removeFrom(name: StorageName): void {
  try {
    window[name].removeItem(STORAGE_KEY);
  } catch {
    // Storage is blocked, so nothing is stored there.
  }
}

import { afterEach, describe, expect, it, vi } from "vitest";
import { changeKeyInAnotherTab } from "./anotherTab";
import { clearApiKey, getApiKey, getRemovedReason, setApiKey, subscribeToApiKey } from "./apiKey";

const STORAGE_KEY = "bioparser.apiKey";

// apiKey.ts keeps a key in memory between tests unless it is cleared.
afterEach(() => {
  vi.restoreAllMocks();
  clearApiKey("forgotten");
  sessionStorage.clear();
  localStorage.clear();
});

// Makes every storage write fail, as in a browser that blocks storage or has none left.
function blockStorageWrites(): void {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("Storage is full", "QuotaExceededError");
  });
}

describe("apiKey", () => {
  it("keeps the key in sessionStorage when not remembered", () => {
    setApiKey("tab-key", false);
    expect(sessionStorage.getItem(STORAGE_KEY)).toBe("tab-key");
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(getApiKey()).toBe("tab-key");
  });

  it("keeps the key in localStorage when remembered", () => {
    setApiKey("device-key", true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe("device-key");
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(getApiKey()).toBe("device-key");
  });

  it("removes a remembered key when a new key is not remembered", () => {
    setApiKey("old-key", true);
    setApiKey("new-key", false);
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(getApiKey()).toBe("new-key");
  });

  it("prefers this tab's key over a remembered one", () => {
    localStorage.setItem(STORAGE_KEY, "device-key");
    sessionStorage.setItem(STORAGE_KEY, "tab-key");
    expect(getApiKey()).toBe("tab-key");
  });

  it("forgets the key in both storages and records why", () => {
    sessionStorage.setItem(STORAGE_KEY, "tab-key");
    localStorage.setItem(STORAGE_KEY, "device-key");
    clearApiKey("rejected");
    expect(getApiKey()).toBeNull();
    expect(getRemovedReason()).toBe("rejected");
  });

  it("clears the reason when a new key is set", () => {
    clearApiKey("rejected");
    setApiKey("new-key", false);
    expect(getRemovedReason()).toBeNull();
  });

  it("keeps the key in memory when storage refuses it, until it is forgotten", () => {
    blockStorageWrites();
    setApiKey("memory-key", true);
    expect(getApiKey()).toBe("memory-key");
    clearApiKey("forgotten");
    expect(getApiKey()).toBeNull();
  });

  it("finds no key when reading storage fails", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("Access denied", "SecurityError");
    });
    expect(getApiKey()).toBeNull();
  });

  it("calls listeners on every change until they unsubscribe", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToApiKey(listener);
    setApiKey("key", false);
    clearApiKey("forgotten");
    expect(listener).toHaveBeenCalledTimes(2);

    unsubscribe();
    setApiKey("key", false);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  describe("apiKey and other tabs", () => {
    it("notices when another tab removes the remembered key", () => {
      setApiKey("shared-key", true);
      const listener = vi.fn();
      const unsubscribe = subscribeToApiKey(listener);
      changeKeyInAnotherTab(null);
      unsubscribe();
      expect(getApiKey()).toBeNull();
      expect(getRemovedReason()).toBe("elsewhere");
      expect(listener).toHaveBeenCalledTimes(1);
    });

    it("notices when another tab saves a remembered key", () => {
      clearApiKey("rejected");
      const listener = vi.fn();
      const unsubscribe = subscribeToApiKey(listener);
      changeKeyInAnotherTab("shared-key");
      unsubscribe();
      expect(getApiKey()).toBe("shared-key");
      expect(getRemovedReason()).toBeNull();
      expect(listener).toHaveBeenCalledTimes(1);
    });

    it("keeps this tab's own key when another tab removes the remembered one", () => {
      localStorage.setItem(STORAGE_KEY, "shared-key");
      sessionStorage.setItem(STORAGE_KEY, "tab-key");
      changeKeyInAnotherTab(null);
      expect(getApiKey()).toBe("tab-key");
      expect(getRemovedReason()).toBeNull();
    });

    it("ignores changes to other storage entries", () => {
      const listener = vi.fn();
      const unsubscribe = subscribeToApiKey(listener);
      window.dispatchEvent(new StorageEvent("storage", { key: "something-else", newValue: "x" }));
      unsubscribe();
      expect(listener).not.toHaveBeenCalled();
    });
  });
});

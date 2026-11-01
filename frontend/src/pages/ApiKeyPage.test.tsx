import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearApiKey, getApiKey, setApiKey } from "../api/apiKey";
import { ApiKeyPage } from "./ApiKeyPage";

const STORAGE_KEY = "bioparser.apiKey";

afterEach(() => {
  cleanup();
  clearApiKey("forgotten");
  sessionStorage.clear();
  localStorage.clear();
});

// Types a key into the form, ticks "Remember" if asked, and presses Save.
function saveKey(key: string, remember = false): void {
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: key } });
  if (remember) {
    fireEvent.click(screen.getByLabelText("Remember on this device"));
  }
  fireEvent.click(screen.getByRole("button", { name: "Save key" }));
}

describe("ApiKeyPage", () => {
  it("keeps the key for this tab by default", () => {
    render(<ApiKeyPage />);
    saveKey("secret-key");
    expect(sessionStorage.getItem(STORAGE_KEY)).toBe("secret-key");
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(screen.getByRole("button", { name: "Forget key" })).toBeDefined();
  });

  it("remembers the key on this device when ticked", () => {
    render(<ApiKeyPage />);
    saveKey("secret-key", true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe("secret-key");
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("trims spaces and line breaks around the key", () => {
    render(<ApiKeyPage />);
    saveKey("  secret-key\n");
    expect(getApiKey()).toBe("secret-key");
  });

  it("does not save a key of only spaces", () => {
    render(<ApiKeyPage />);
    saveKey("   ");
    expect(getApiKey()).toBeNull();
    expect(screen.getByRole("button", { name: "Save key" })).toBeDefined();
  });

  it("never shows the key after saving", () => {
    render(<ApiKeyPage />);
    saveKey("secret-key");
    expect(screen.queryByDisplayValue("secret-key")).toBeNull();
    expect(document.body.textContent).not.toContain("secret-key");
  });

  it("forgets the key and says so", () => {
    setApiKey("secret-key", false);
    render(<ApiKeyPage />);
    fireEvent.click(screen.getByRole("button", { name: "Forget key" }));
    expect(getApiKey()).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("The key was forgotten.");
    expect(screen.getByRole("button", { name: "Save key" })).toBeDefined();
  });

  it("explains that the API did not accept the key", () => {
    setApiKey("wrong-key", false);
    render(<ApiKeyPage />);
    act(() => {
      clearApiKey("rejected");
    });
    expect(screen.getByRole("alert").textContent).toBe(
      "The API did not accept the key. Enter it again.",
    );
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows no message on a first visit", async () => {
    // A fresh copy of the modules, as after a page load: no key has been removed yet.
    vi.resetModules();
    const { ApiKeyPage: FreshApiKeyPage } = await import("./ApiKeyPage");
    render(<FreshApiKeyPage />);
    expect(screen.getByRole("button", { name: "Save key" })).toBeDefined();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });
});

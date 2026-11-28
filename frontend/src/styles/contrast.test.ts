import { describe, expect, it } from "vitest";
// jsdom computes no styles, so the token values are read from the stylesheet itself.
import css from "./tokens.css?raw";

const darkStart = css.indexOf("@media (prefers-color-scheme: dark)");

function colors(block: string): Map<string, string> {
  const found = new Map<string, string>();
  for (const match of block.matchAll(/--(color-[a-z0-9-]+):\s*(#[0-9a-f]{6});/g)) {
    found.set(match[1] as string, match[2] as string);
  }
  return found;
}

const light = colors(css.slice(0, darkStart));
// A token the dark block leaves out keeps its light value.
const dark = new Map([...light, ...colors(css.slice(darkStart))]);

// WCAG 2 relative luminance and contrast ratio.
function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * (r as number) + 0.7152 * (g as number) + 0.0722 * (b as number);
}

function contrast(a: string, b: string): number {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return ((high as number) + 0.05) / ((low as number) + 0.05);
}

// Text pairs need 4.5:1. Every text color is checked on each surface it is used on.
const TEXT: [string, string][] = [
  ["color-text", "color-bg"],
  ["color-text", "color-surface"],
  ["color-text", "color-surface-muted"],
  ["color-text", "color-surface-selected"],
  ["color-text-muted", "color-bg"],
  ["color-text-muted", "color-surface"],
  ["color-text-muted", "color-surface-muted"],
  // The labels of an expanded result row, when that row is the selected one.
  ["color-text-muted", "color-surface-selected"],
  ["color-accent", "color-bg"],
  ["color-accent", "color-surface"],
  ["color-accent", "color-surface-selected"],
  ["color-accent-strong", "color-surface"],
  ["color-on-accent", "color-accent"],
  ["color-header-text", "color-header-bg"],
  ["color-header-muted", "color-header-bg"],
  ["color-header-current-text", "color-header-current-bg"],
  ["color-danger", "color-surface"],
  ["color-danger", "color-danger-soft"],
  // The Delete job button sits on the question's muted panel.
  ["color-danger", "color-surface-muted"],
  ["color-warning", "color-surface"],
  // Warnings in an expanded result row, plain or on the selected row's tint.
  ["color-warning", "color-surface-muted"],
  ["color-warning", "color-surface-selected"],
  ["color-status-queued", "color-status-queued-bg"],
  ["color-status-running", "color-status-running-bg"],
  ["color-status-parsed", "color-status-parsed-bg"],
  ["color-status-done", "color-status-done-bg"],
  ["color-status-failed", "color-status-failed-bg"],
];

// Control borders, focus rings and progress steps need 3:1 against what is next to them.
const NON_TEXT: [string, string][] = [
  ["color-border-strong", "color-surface"],
  ["color-border-strong", "color-bg"],
  ["color-focus", "color-surface"],
  ["color-focus", "color-bg"],
  ["color-focus-on-header", "color-header-bg"],
  ["color-step-done", "color-surface"],
  ["color-step-active", "color-surface"],
  ["color-step-failed", "color-surface"],
  ["color-mark-value-line", "color-surface"],
  ["color-mark-unit-line", "color-surface"],
];

describe.each([
  ["light", light],
  ["dark", dark],
])("%s colors", (_scheme, tokens) => {
  function ratio(foreground: string, background: string): number {
    const a = tokens.get(foreground);
    const b = tokens.get(background);
    if (a === undefined || b === undefined) {
      throw new Error(`Missing token ${a === undefined ? foreground : background}`);
    }
    return contrast(a, b);
  }

  it.each(TEXT)("%s on %s is at least 4.5:1", (foreground, background) => {
    expect(ratio(foreground, background)).toBeGreaterThanOrEqual(4.5);
  });

  it.each(NON_TEXT)("%s next to %s is at least 3:1", (foreground, background) => {
    expect(ratio(foreground, background)).toBeGreaterThanOrEqual(3);
  });
});

describe("tokens", () => {
  it("gives every light color a dark value", () => {
    const darkOnly = colors(css.slice(darkStart));
    const missing = [...light.keys()].filter((name) => !darkOnly.has(name));
    expect(missing).toEqual([]);
  });
});

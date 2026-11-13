import { useSyncExternalStore } from "react";
import { getApiKey, subscribeToApiKey } from "./apiKey";

// Whether a key is stored. A component that calls this redraws when a key is set or removed.
// It returns a boolean, not the key, so no component ever holds the key and can show it.
export function useHasApiKey(): boolean {
  return useSyncExternalStore(subscribeToApiKey, hasApiKey);
}

function hasApiKey(): boolean {
  return getApiKey() !== null;
}

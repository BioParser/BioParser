import { useSyncExternalStore } from "react";
import { getRemovedReason, type RemovedReason, subscribeToApiKey } from "./apiKey";

// Why the key was last removed, or null. A component that calls this redraws when it changes.
export function useRemovedReason(): RemovedReason | null {
  return useSyncExternalStore(subscribeToApiKey, getRemovedReason);
}

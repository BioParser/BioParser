import { QueryClient } from "@tanstack/react-query";
import { getApiKey, subscribeToApiKey } from "./apiKey";
import { ApiError } from "./client";

// React Query's default: a failed request is tried up to 3 more times.
const MAX_RETRIES = 3;

export function createQueryClient(): QueryClient {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: shouldRetry },
    },
  });
  // Data loaded with a key is dropped when the key is forgotten or rejected, so it is not
  // shown again to whoever enters the next key. The pages load it again with the new key.
  subscribeToApiKey(() => {
    if (getApiKey() === null) {
      queryClient.clear();
    }
  });
  return queryClient;
}

// A 401 is not tried again: the key was wrong, so the same request fails the same way.
// The key screen asks for a new key instead.
export function shouldRetry(failureCount: number, error: Error): boolean {
  if (error instanceof ApiError && error.status === 401) {
    return false;
  }
  return failureCount < MAX_RETRIES;
}

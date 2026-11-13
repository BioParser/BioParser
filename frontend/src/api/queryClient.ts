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

// A 4xx error means the request itself was refused: a wrong key (401), an unknown job (404),
// invalid input (422). Sent again, it fails the same way, so it is not retried. 408 (timeout)
// and 429 (too many requests) are the exceptions: they can succeed later.
export function shouldRetry(failureCount: number, error: Error): boolean {
  if (error instanceof ApiError && isFinalClientError(error.status)) {
    return false;
  }
  return failureCount < MAX_RETRIES;
}

function isFinalClientError(status: number): boolean {
  return status >= 400 && status < 500 && status !== 408 && status !== 429;
}

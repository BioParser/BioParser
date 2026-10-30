import type { components, paths } from "./schema";

type Schemas = components["schemas"];

export type ErrorCode = Schemas["ErrorDetail"]["code"];
export type QueuedJobResponse = Schemas["QueuedJobResponse"];
// JobStatusResponse is a union in Python, not a model, so it has no entry in the schemas.
export type JobStatusResponse =
  paths["/api/jobs/{job_id}"]["get"]["responses"][200]["content"]["application/json"];

export class ApiError extends Error {
  readonly status: number;
  // The API's error code, when the body is {"detail": {"code", "message"}}.
  readonly code: ErrorCode | undefined;

  constructor(status: number, code: ErrorCode | undefined, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

function isErrorDetail(value: unknown): value is Schemas["ErrorDetail"] {
  return (
    typeof value === "object" &&
    value !== null &&
    "code" in value &&
    "message" in value &&
    typeof value.code === "string" &&
    typeof value.message === "string"
  );
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    // Not JSON, e.g. a proxy error page.
    body = undefined;
  }
  const detail =
    typeof body === "object" && body !== null && "detail" in body ? body.detail : undefined;
  if (isErrorDetail(detail)) {
    return new ApiError(response.status, detail.code, detail.message);
  }
  return new ApiError(response.status, undefined, `Request failed with status ${response.status}`);
}

// The one place the UI calls the API. A network failure rejects with fetch's own TypeError.
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as T;
}

export function submitJob(file: File): Promise<QueuedJobResponse> {
  const body = new FormData();
  body.append("file", file);
  // No Content-Type header: the browser sets multipart/form-data with its boundary.
  return apiFetch("/api/extractions", { method: "POST", body });
}

export function getJob(jobId: string): Promise<JobStatusResponse> {
  return apiFetch(`/api/jobs/${encodeURIComponent(jobId)}`);
}

# BioParser API contract and module layout

This document defines the public HTTP contract of the API container and the internal
module layout that implements it. The system context for these endpoints is in
[architecture.md](architecture.md).

Status: **draft** — document and job management routes (`POST /api/documents`,
`GET /api/documents`, `GET /api/documents/{checksum}`, `GET /api/documents/{checksum}/file`,
`POST /api/extractions`, `POST /api/documents/{checksum}/jobs`, `GET /api/documents/{checksum}/jobs`, `GET /api/jobs`,
`GET /api/jobs/{job_id}`, `DELETE /api/jobs/{job_id}`, `GET /api/jobs/{job_id}/ets`,
`GET /api/jobs/{job_id}/artifacts/parser`, `GET /api/jobs/{job_id}/artifacts/extraction`, `GET /health`, `GET /ready`)
are implemented, along with upload validation and a configurable size limit. A done job's
ETS rows are `GET /api/jobs/{job_id}/ets`.

**Current API scope:**

- **Job state is stored in Postgres.** The API and both workers share the typed,
  versioned `JobState` model through `bioparser.db.jobs`. The `Job` class there
  is the table, and Alembic diffs it when generating a revision. Redis is the queue.
- **The parser worker is a separate process.** `POST /api/documents/{checksum}/jobs` enqueues a
  `ParseJobMessage`; `python -m bioparser.worker.parse` consumes it and advances
  the job from `queued` through `parsing` to `parsed` or `failed`.
- **Uploaded PDFs are persisted in artifact storage.** Their paths are derived
  from the content SHA-256, so two submissions of the same PDF create separate
  jobs that reuse one immutable PDF artifact.
- **A done job returns ETS rows.** `GET /api/jobs/{job_id}/ets` loads the stored extraction
  artifact and maps it. Other statuses do not. `?detail=full` on the job read adds the
  stored record and does not include the rows.
- **Readiness (`/ready`) is supported.** The readiness endpoint performs
  concurrent checks of Redis, Postgres, and artifact storage (see *Readiness
  behaviour* below).
- **No `/api/v1` prefix or global exception handlers.** Routes raise
  `fastapi.HTTPException` directly and use structured `{"code", "message"}`
  details for the queued-job API's documented failures.

---

## Scope

The API container accepts a born-digital PDF, creates a job, and lets the client poll
for its status. `POST /api/documents` stores the PDF. `POST /api/documents/{checksum}/jobs`
creates the Postgres job row and enqueues parsing without parsing in the request process.
`POST /api/extractions` does both.
The separate parser worker advances the stored state. A done job's poll returns the ETS
rows for that paper.

---

## HTTP contract

### Authentication

All endpoints require a shared API key, except the explicit public endpoints: `/health`, `/ready`, `/docs`, `/docs/oauth2-redirect`, `/openapi.json`, and `/redoc`.

- **Header:** `X-API-Key: <key>`
- **Configuration:**
  - `BIOPARSER_API_KEY`: The expected secret key (stored as `SecretStr`). Compose gives it as the Docker secret `/run/secrets/bioparser_api_key`. Must be at least 32 visible ASCII characters (without spaces). Generate with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`. Required unless `BIOPARSER_AUTH_DISABLED=true`.
  - `BIOPARSER_AUTH_DISABLED`: Boolean flag (`true`/`false`, default `false`). If `true`, authentication checks are bypassed and a warning is logged at startup. If `false` and `BIOPARSER_API_KEY` is not set or invalid, the API refuses to start.
- **Validation:**
  - Enforced before body parsing by a pure ASGI middleware (`ApiKeyMiddleware`). Unauthenticated requests are rejected with 401 before any upload body is buffered or written to disk.
  - Constant-time comparison (`secrets.compare_digest`) is performed over UTF-8 bytes to safely reject non-ASCII header values without raising exceptions.
- **Failure — `401 Unauthorized`:**
  Missing or invalid keys return HTTP 401 with `WWW-Authenticate: ApiKey` and a standard error envelope:
  ```json
  {
    "detail": {
      "code": "unauthorized",
      "message": "Invalid or missing API key"
    }
  }
  ```
- **OpenAPI / Swagger UI:** The security scheme `ApiKeyAuth` is registered on the OpenAPI document (`/openapi.json`), enabling interactive authentication via the "Authorize" button in Swagger UI (`/docs`).

### Rate limit

Through Traefik (`docker-compose.traefik.yml`), each client IP address has a request limit. Traefik answers a request over the limit with `429 Too Many Requests`, and the request does not go to the API. The response has a `Retry-After` header (in seconds) and a plain-text body, not the error envelope above. Send the request again after that time. The API port itself has no rate limit. [docker-compose.md](docker-compose.md#https-and-traefik) gives the values.

### `POST /api/extractions`

Submit a PDF.

- **Request:** `multipart/form-data` with a single `file` part and an optional `title` field.
- **Title:** leading and trailing whitespace is trimmed, control characters are removed,
  and quotes are preserved. A title longer than 500 characters after cleaning returns
  `422` with `{"detail": "Title must be at most 500 characters."}`. A non-empty title
  replaces the stored title when uploading an existing PDF; an omitted or blank title
  keeps it. The title belongs to the document and is shared by every job of that PDF.
- **Accepted content type:** `application/pdf` only, checked against the `file`
  part's own `content_type`.
- **Body checks:** the upload must be non-empty and must begin with the `%PDF-`
  magic bytes.
- **Size limit:** the upload is read in 1 MiB chunks and rejected once it exceeds
  `config.MAX_UPLOAD_BYTES` (20 MiB by default — see [`config.py`](#configpy)).
- Submitting the same file twice creates two separate jobs. The PDF artifact is
  content-addressed by SHA-256 and reused by both jobs. The document row and the
  job row are inserted in one transaction. A checksum that already has a document
  keeps that row, including its display name. A new document stores the sanitized
  upload filename and the upload size.

Title validation runs before the upload checks below.

The checks run in a fixed order: `Content-Length` header format and size →
`file` part present → content type → body size while streaming → non-empty body →
`%PDF-` magic. The first one to fail is the one reported.

**Success — `202 Accepted`:**

```json
{
  "job_id": "b3f1c2a4-8d7e-4a65-9e31-123456789abc",
  "status": "queued"
}
```

The stored name is on the document and on a later job read. A failed insert stores
no job. When the job insert fails, a document row from this request is rolled back
with it.

**Failure:**

| Status | When |
|--------|------|
| 400 | no `file` part in the request |
| 400 | the file body is empty |
| 400 | the file body does not start with `%PDF-` |
| 400 | the Content-Length header is not a valid non-negative integer |
| 413 | the file exceeds BIOPARSER_MAX_UPLOAD_BYTES (default 20 MiB) |
| 415 | content type is not `application/pdf` |
| 422 | title exceeds 500 characters after cleaning |
| 503 | artifact storage, the database, or the parse queue is unavailable |


**Validation error body:**

- Size-limit failures (`413`) use Starlette's plain-text body `Content Too Large`
  (same as `RequestBodyLimitMiddleware`).
- Title length validation uses the string `detail` shown above. Other upload validation
  failures respond with FastAPI's `detail` wrapper:

```json
{
  "detail": {
    "code": "invalid_pdf",
    "message": "File does not look like a PDF"
  }
}
```
`code` is one of a fixed set of values:

| Code | Status | Meaning |
|------|--------|---------|
| `missing_file` | 400 | no `file` part in the request |
| `empty_file` | 400 | the `file` part is present but its body is empty |
| `invalid_pdf` | 400 | the body does not start with the `%PDF-` magic bytes |
| `invalid_content_length`| 400 | Content-Length header is not a valid non-negative integer |
| `unsupported_content_type` | 415 | content type is not `application/pdf` |


### `POST /api/documents`

Upload and store a PDF document without queueing a job.

- **Request:** `multipart/form-data` with a single `file` part and an optional `title` field.
- **Title:** the same cleaning, 500-character limit and re-upload rules as
  `POST /api/extractions` above. A non-empty title replaces the stored title;
  omitting it or sending a blank title keeps the existing title.
- **Accepted content type:** `application/pdf` only.
- **Body checks:** Content-Length, a present `file` part, media type `application/pdf`, the size limit, a non-empty body, and the `%PDF-` magic. The first check to fail is the one reported.
- Content-addressed by SHA-256. If the document already exists, the existing metadata is returned with `200 OK`. If newly stored, returns `201 Created`.

**Success — `201 Created` (new) or `200 OK` (already stored):**

```json
{
  "checksum": "5685e2d63d2a3b750e0850b8654c06f87fe9a1b138525deef264166e4152efbc",
  "display_name": "paper.pdf",
  "upload_time": "2026-10-26T12:00:00Z",
  "size_bytes": 1048576,
  "title": "Example paper"
}
```

**Failure:** `400` (`missing_file`, `empty_file`, `invalid_pdf`, `invalid_content_length`), `413` (`content_too_large`), `415` (`unsupported_content_type`), `422` (title exceeds 500 characters), `503` (`storage_unavailable`, `database_unavailable`).


### `GET /api/documents`

List all stored documents with metadata, ordered by `upload_time` descending.

**Success — `200 OK`:**

```json
[
  {
    "checksum": "5685e2d63d2a3b750e0850b8654c06f87fe9a1b138525deef264166e4152efbc",
    "display_name": "paper.pdf",
    "upload_time": "2026-10-26T12:00:00Z",
    "size_bytes": 1048576,
    "title": "Example paper"
  }
]
```

**Failure:** `401 Unauthorized`, `503 Database Unavailable` (`database_unavailable`).


### `GET /api/documents/{checksum}`

Retrieve metadata for a specific stored document.

**Success — `200 OK`:**

```json
{
  "checksum": "5685e2d63d2a3b750e0850b8654c06f87fe9a1b138525deef264166e4152efbc",
  "display_name": "paper.pdf",
  "upload_time": "2026-10-26T12:00:00Z",
  "size_bytes": 1048576,
  "title": "Example paper"
}
```

**Failure:**

| Status | When |
|--------|------|
| 401 | Missing or invalid API key (`unauthorized`) |
| 404 | No document with that checksum (`document_not_found`) |
| 422 | `checksum` is not a 64-character lowercase SHA-256 hex digest (`invalid_checksum`) |
| 503 | Database is unavailable (`database_unavailable`) |


### `PATCH /api/documents/{checksum}`

Set or clear the title shared by every job of a stored PDF. The filename is kept.

**Request Body (JSON):**

```json
{
  "title": "Darwin's paper"
}
```

`title` is required. Set it to `null` or a blank string to clear it. Titles use the
same cleaning and 500-character limit as uploads. Extra fields are rejected.

**Success — `200 OK`:** the updated document metadata, with the same fields as
`GET /api/documents/{checksum}`. `title` is `null` when cleared or never set.
Subsequent document reads and full job reads return the current title.

**Failure:**

| Status | When |
|--------|------|
| 401 | Missing or invalid API key (`unauthorized`) |
| 404 | No document with that checksum (`document_not_found`) |
| 422 | Invalid checksum, missing or invalid `title`, extra fields, or title exceeds 500 characters |
| 503 | Database is unavailable (`database_unavailable`) |


### `GET /api/documents/{checksum}/file`

Serve raw bytes of a stored PDF document.

**Success — `200 OK`:**
- Content type: `application/pdf`
- Headers:
  - `Content-Disposition: inline; filename="<ascii_fallback>"; filename*=UTF-8''<encoded_name>`
  - `Cache-Control: private, max-age=31536000, immutable`

**Failure:** Same 401, 404, 422, and 503 statuses as `GET /api/documents/{checksum}`. `500` if the stored payload is corrupted.


### `POST /api/documents/{checksum}/jobs`

Dispatch a parsing and extraction job for an already-stored document with configurable backend parameters.

- **Request Body (JSON, optional):**
```json
{
  "parser": "default",
  "extractor": "default"
}
```
If omitted or `{}` is sent, parameters default to `parser: "default"` and `extractor: "default"`.

**Success — `202 Accepted`:**

```json
{
  "job_id": "b3f1c2a4-8d7e-4a65-9e31-123456789abc",
  "status": "queued"
}
```

**Failure:**

| Status | When |
|--------|------|
| 401 | Missing or invalid API key (`unauthorized`) |
| 404 | Document does not exist (`document_not_found`) |
| 422 | `checksum` invalid, or unknown `parser`/`extractor` |
| 500 | Stored job state is unreadable (`job_state_unreadable`) |
| 503 | Storage, Redis, or queue is unavailable |


### `GET /api/documents/{checksum}/jobs`

List all jobs for `{checksum}`. The default row is `job_id` and `status`. `?detail=full` adds `created_at`, `display_name`, `title`, `parser`, `extractor`, `document_checksum`, and `has_parse_result`. `display_name` is null when the document has no name; `title` is null when no title is set. A done job does not include its ETS rows here. Those are `GET /api/jobs/{job_id}/ets`.

**Success — `200 OK`:**

```json
[
  {
    "job_id": "b3f1c2a4-8d7e-4a65-9e31-123456789abc",
    "status": "queued"
  }
]
```

**Failure:** Same 401, 404, 422, 500, and 503 statuses as `POST /api/documents/{checksum}/jobs`.


### `GET /api/jobs`

List jobs across all documents, with optional query filters. The default row is `job_id` and `status`. `?detail=full` adds the stored record from the job joined to its document, including its current `title`, and still omits ETS rows. `display_name` is null when the document has no name; `title` is null when no title is set.

- **Query parameters:**
  - `status` (optional): Filter by job status (`queued`, `parsing`, `parsed`, `extracting`, `done`, `failed`).
  - `parser` (optional): Filter by parser backend (e.g. `default`, `mineru`).

**Success — `200 OK`:**

```json
[
  {
    "job_id": "b3f1c2a4-8d7e-4a65-9e31-123456789abc",
    "status": "queued"
  }
]
```

**Failure:**

| Status | When |
|--------|------|
| 401 | Missing or invalid API key (`unauthorized`) |
| 422 | Invalid `status` query value |
| 500 | Stored job state is unreadable (`job_state_unreadable`) |
| 503 | Database is unavailable (`database_unavailable`) |


### `GET /api/jobs/{job_id}`

Poll a job.

**Success — `200 OK`:**

```json
{
  "job_id": "b3f1c2a4-8d7e-4a65-9e31-123456789abc",
  "status": "queued"
}
```

- `queued`, `parsing`, `parsed`, and `extracting` return `job_id` and `status`.
- `failed` adds `error`, a safe error code.
- `done` is `job_id` and `status`. The ETS rows are `GET /api/jobs/{job_id}/ets`, in
  observation order. `references` on each row is the document checksum.
- `?detail=full` adds `created_at`, `display_name`, `title`, `parser`, `extractor`,
  `document_checksum`, and `has_parse_result`. That read is one query, the job
  joined to its document. `display_name` is null when the document has no name.
  `title` is the document's current title, or null when no title is set.
  The flag is true when the parser artifact was stored. `timeout` and
  `internal_error` do not say which stage ended, so a full read is what tells a
  reader that parsing finished. The full body has no rows.
- The response holds no storage paths. The UI reads the internal artifacts from the
  [job artifact routes](#get-apijobsjob_idartifactsparser-and-artifactsextraction).

**Failure:**

| Status | When |
|--------|------|
| 404 | no job with that id |
| 422 | `job_id` is not a version 4 UUID |
| 500 | stored job state is unreadable |
| 503 | the database is unavailable |

The status read does not load the extraction artifact. A done job is `job_id` and `status`.

### `DELETE /api/jobs/{job_id}`

Delete a finished job. Only a `done` or `failed` job can be deleted: a job in any other
status still has a message in the queue and a worker that moves it on.

**Success — `204 No Content`**, with no body. The job no longer appears in `GET /api/jobs`
or `GET /api/documents/{checksum}/jobs`, and `GET /api/jobs/{job_id}` and the job's
`/ets` and `/artifacts/...` routes are `404`.

- It is a soft delete. The job row stays in the database with `deleted_at` set, so a
  later undo or trash view can bring it back. No route does that yet.
- The document, its PDF and the parser and extraction artifacts stay. Artifacts are
  keyed by the PDF checksum and the parser and extractor versions, so other jobs of the
  same PDF can share them.
- The status check and the write are one statement, so a job that moves to another
  status in between is not deleted. The `jobs_deleted_at_check` constraint also keeps
  `deleted_at` off a job that is not `done` or `failed`.

**Failure:**

| Status | When |
|--------|------|
| 401 | missing or invalid API key (`unauthorized`) |
| 404 | no job with that id (`job_not_found`), including one already deleted |
| 409 | the job is not `done` or `failed` (`job_not_finished`); the row stays |
| 422 | `job_id` is not a version 4 UUID (`invalid_job_id`) |
| 503 | the database is unavailable (`database_unavailable`) |

### `GET /api/jobs/{job_id}/ets`

The ETS rows for a done job, in observation order.

**Success — `200 OK`:**

```json
{
  "rows": []
}
```

**Failure:**

| Status | When |
|--------|------|
| 404 | no job with that id |
| 409 | the job is not done (`artifact_not_available`) |
| 422 | `job_id` is not a version 4 UUID |
| 500 | stored job state is unreadable, or the extraction artifact is missing or invalid (`artifact_invalid`) |
| 503 | the database is unavailable, or artifact storage is unavailable |

The file read and the mapping run off the event loop.

**Error body:**

```json
{
  "detail": {
    "code": "job_not_found",
    "message": "Job not found"
  }
}
```

### `GET /api/jobs/{job_id}/artifacts/parser` and `/artifacts/extraction`

Internal routes for the BioParser UI (OpenAPI tag `internal`): a PDF viewer needs the
bounding boxes of the parser artifact, and an evidence view needs the extraction artifact.
API clients use `GET /api/jobs/{job_id}` instead. The body is the artifact exactly as the
worker stored it (`application/json`). Its shape follows its `schema_version` and can
change.

| Route | Available when the job is |
|-------|---------------------------|
| `.../artifacts/parser` | `parsed`, `extracting`, `done`, or `failed` after its parse |
| `.../artifacts/extraction` | `done` |

- The route loads the stored bytes through async storage and does not parse them: the worker
  validated them before it recorded the ref, and a parser artifact can be several MB.
- `Cache-Control: private, max-age=31536000, immutable`. A job's artifact never changes:
  storage cannot replace an artifact, and the job keeps the same ref.
- The PDF itself is not served here. A viewer gets it from the document file route that
  #197 adds.

**Failure:**

| Status | When |
|--------|------|
| 404 | no job with that id (`job_not_found`) |
| 409 | the job has no such artifact (`artifact_not_available`); its status tells whether it can still come |
| 422 | `job_id` is not a version 4 UUID (`invalid_job_id`) |
| 500 | stored job state is unreadable, or the stored artifact is missing or invalid |
| 503 | Redis or artifact storage is unavailable |

### `GET /health`

Liveness. Returns `200 {"status": "ok"}` whenever the process is running. No
dependencies are touched.

**Success — `200 OK`:**

```json
{
  "status": "ok"
}
```

### `GET /ui`

The web UI, built from `frontend/` (see the README). Set `BIOPARSER_UI_DIST_PATH` to the
built files (`frontend/dist`); the API image sets it. Without it, every `/ui` path is `404`.

- `/ui` redirects to `/ui/`.
- A path under `/ui/` that names a built file returns that file. Files under `assets/` are
  cached for a year (`immutable`): their names hold a content hash.
- Any other path returns `index.html` (`Cache-Control: no-cache`), so a deep link such as
  `/ui/jobs/{job_id}` loads the app. A missing file under `assets/` is `404`.
- Not in the OpenAPI schema.
- No API key: the UI is static files with no data, and a browser cannot send `X-API-Key` when it opens a page. The UI sends the key on its own API calls (#253). `ApiKeyMiddleware` lets `/ui` and every path under `/ui/` through.


### `GET /ready`

Readiness. Checks Redis, Postgres, and the artifact directory concurrently, each
with a bounded timeout, and returns `200 {"status": "ready"}` when all three succeed.

**Success — `200 OK`:**

```json
{
  "status": "ready"
}
```

**Failure — `503 Service Unavailable`:**

```json
{
  "detail": {
    "status": "not ready",
    "unavailable": ["redis"]
  }
}
```

Readiness behaviour:

- The endpoint parses the Redis URL and attempts a TCP connection to the host and port
  from that URL. The per-check timeout is `BIOPARSER_DEPENDENCY_CHECK_TIMEOUT_SECONDS`
  (seconds, default `1.0`). On failure the endpoint returns `503`; FastAPI places the
  readiness payload under `detail`, e.g.
  `{"detail": {"status": "not ready", "unavailable": ["redis"]}}`.
- It runs `SELECT 1` through the API's existing Postgres pool, using the configured
  database credentials. The same per-check timeout covers borrowing a connection
  and completing the transaction. A connection failure, failed query, or timeout
  returns `503` with `"postgres"` in the `unavailable` list. The response does not
  include the URL or its password.
- It attempts to create and remove a temporary file in the artifact directory to confirm
  writability. On failure `503` is returned with `"storage"` in the `unavailable` list.
- Invalid configuration, including an empty required setting, returns `503` with
  `"config"` in the `unavailable` list. The API also refuses to start when these
  settings are missing.

All three checks start together, so a slow dependency does not delay the start of
the other checks. The endpoint waits for all results and lists every failed
dependency in Redis, Postgres, storage order. Redis uses a raw TCP connection;
Postgres must accept an authenticated query; storage uses a local file operation.

---

## Module layout

```
src/bioparser/api/
  __init__.py       # main() -> starts the app
  app.py            # FastAPI app, lifespan, and the route handlers
  job_responses.py  # the short and full public job bodies
  auth.py           # API key authentication dependency (X-API-Key)
  config.py         # process configuration read from the environment
  errors.py         # named ErrorDetail constants, codes, and OpenAPI helpers
  middleware.py     # body size limit middleware
  schema.py         # pydantic response/error models used across routes
  uploads.py        # upload-validation helpers shared by the upload routes
```

No `ports/` or `adapters/` folders exist inside `api/` itself. Routes live
directly in `app.py`. `job_responses.py` builds the short and full public job
bodies. Upload validation is in `uploads.py`. Response and error models are in
`schema.py`.

### `app.py`

Holds the FastAPI app, its startup/shutdown lifecycle, and the route handlers.

**Lifespan:** on startup, first calls `setup_logging(settings.log_level)` (see
[Logging](#logging)). When configured, the lifecycle opens the async Postgres pool
(`bioparser.db.engine`, asyncpg), a Redis-backed `DramatiqJobSubmitter`, and a
`FileSystemArtifactStorage`. On shutdown, network clients and the Postgres pool are closed.
Migrations stay on the sync psycopg driver.

- `POST /api/extractions` — runs `_validated_upload()` (the [`uploads.py`](#uploadspy) helpers,
  in order: `validate_content_length`, `require_file`, `validate_content_type`,
  `read_upload`, `validate_pdf_content`), persists the content-addressed PDF, then
  inserts the document row and the job in one Postgres transaction. A checksum that
  already has a document keeps that row. The 202 body is `AcceptedJobResponse`
  (`job_id` and `status`). `POST /api/documents/{checksum}/jobs` returns the same
  body. The enqueue of a `ParseJobMessage` is outside that transaction. A failed
  enqueue marks that job `failed` in a later transaction.
  Starlette's `RequestBodyLimitMiddleware` aborts an oversized body while it is still
  being received. A missing or understated `Content-Length` is still capped while
  reading the file in chunks.
- `GET /api/jobs/{job_id}` — reads the job joined to its document. It does not read the
  extraction artifact. `?detail=full` adds the stored record, including the document's
  display name.
- `DELETE /api/jobs/{job_id}` — soft-deletes the job with `bioparser.db.jobs.delete_finished`,
  one `UPDATE jobs SET deleted_at = now() WHERE status IN ('done', 'failed') AND
  deleted_at IS NULL`. When no row changed, a second read tells `404` (no row, or already
  deleted) from `409`. Storage is not touched. `list_jobs` and `list_jobs_for_document`
  leave deleted jobs out, so every job read of the API does too. `jobs.get`, which only
  the workers use, still returns a deleted job: they skip a `done` or `failed` job.
- `GET /api/jobs/{job_id}/ets` — for a done job, reads the extraction artifact off the
  event loop and returns the ETS rows. Another status is `409`.
- `GET /api/jobs/{job_id}/artifacts/parser` and `.../extraction` — read the job, return `409`
  when it has no such ref, then return the stored bytes via async storage (disk I/O runs
  on a thread inside the storage implementation).
- `GET /health` — returns `HealthResponse(status="ok")` directly, no dependencies.
- `GET /ready` — checks Redis, Postgres, and the artifact directory.

Errors use `fastapi.HTTPException` directly — raised from the `uploads.py` helpers for
upload validation, and inline in `job_status` for a missing job or a bad artifact. A `413`
handler maps oversize errors to Starlette's plain-text `Content Too Large` body.

### `uploads.py`

Stateless helpers that validate an upload, shared by `POST /api/documents` and
`POST /api/extractions`. Each raises `fastapi.HTTPException` with a `{"code", "message"}`
object as its `detail` on failure, and returns normally on success.

- `require_file(file) -> UploadFile` — raises `400 missing_file` when no `file`
  part was sent; otherwise returns it.
- `validate_content_type(file) -> None` — raises `415 unsupported_content_type`
  unless the MIME type (case-insensitive, parameters stripped) is `application/pdf`.
- `validate_content_length(header) -> None` — no-op when the header is absent;
  raises `400 invalid_content_length` when it is not a non-negative integer;
  raises `413` (`Content Too Large`) when it exceeds `max_upload_bytes`.
- `read_upload(file) -> bytes` — reads the upload in 1 MiB chunks, raising
  `413` (`Content Too Large`) as soon as the running total exceeds
  `get_api_settings().max_upload_bytes`; returns the full body otherwise.
- `validate_pdf_content(content) -> None` — raises `400 empty_file` for an empty
  body, and `400 invalid_pdf` if the body does not start with the `%PDF-` magic
  bytes.

### `schema.py`

Pydantic models shared by the routes above, used both for response validation and
for the OpenAPI docs' `responses=` examples:

- `JobStatusResponse` — the shapes for one job, on `GET /api/jobs/{job_id}` and in a
  list. `JobResponse` is `job_id` and `status`. `JobFullResponse` adds the stored
  record for `?detail=full`. `FailedJobResponse` carries `error`, and
  `FailedJobFullResponse` adds the record. None of these include ETS rows.
  `EtsRowsResponse` is `GET /api/jobs/{job_id}/ets`.
  `POST /api/extractions` and `POST /api/documents/{checksum}/jobs` return
  `AcceptedJobResponse`, which is `job_id` and `status`.
- `HealthResponse` — `{status: Literal["ok"]}`, returned by `GET /health`.

### `errors.py`

Error types and the named `ErrorDetail` instances used across `uploads.py` and `app.py`.

- `ErrorCode` — the fixed set of validation, job-state, storage, database, and queue
  error codes returned by the API.
- `ErrorDetail` — `{code: ErrorCode, message: str}`, the shape used as
  `HTTPException.detail` for typed API failures.
- `ErrorResponse` — `{detail: ErrorDetail}`, used only in `responses=` schemas for
  OpenAPI documentation; it is never constructed at runtime.
- Named `ErrorDetail` constants provide one source of truth for each
  validation, job-state, storage, database, and queue error's code and message.
- `http_error(status_code, detail)` — builds the `HTTPException` raised at each
  failure site from one of the constants above.
- `error_response(*details)` — builds the `responses=` OpenAPI entry (schema +
  named examples) for a route decorator from the same constants.

### `config.py`

Process configuration via pydantic `BaseSettings` (`ApiSettings`), loaded once
through `get_api_settings()` (cached) and read from a `.env` file plus
`BIOPARSER_`-prefixed environment variables. A field can also come from a Docker secret:
the file with the variable's name in `/run/secrets`, for example
`/run/secrets/bioparser_redis_password`. A variable has priority over the file. Fields:

| Field | Default | Meaning |
|-------|---------|---------|
| `max_upload_bytes` | `DEFAULT_MAX_UPLOAD_BYTES` (20 MiB) | upload ceiling used by `uploads.py` and the body-limit middleware |
| `dependency_check_timeout_seconds` | `1.0` | timeout for each readiness dependency check |
| `artifact_storage_path` | required | filesystem artifact root |
| `redis_url` | required | Redis URL for the parse queue |
| `redis_password` | `None` | Redis password (`SecretStr`), for a `redis_url` without one |
| `redis_timeout_seconds` | `5.0` | Redis connect and operation timeout |
| `database_url` | required | `postgresql` URL (`PostgresDsn`) for job state; `/ready` runs `SELECT 1` through the API's pool |
| `database_password` | `None` | Postgres password (`SecretStr`), for a `database_url` without one |
| `parse_queue_name` | `parse` | Redis/Dramatiq queue name shared with the parser worker |
| `api_key` | `None` | Shared API key (`BIOPARSER_API_KEY`). Required when `auth_disabled` is false. Redacted in logs and errors (`SecretStr`). |
| `auth_disabled` | `False` | Disables authentication checks for local development (`BIOPARSER_AUTH_DISABLED`). Emits a warning at startup if true. |
| `log_level` | `INFO` | level of the `bioparser` loggers (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`; case-insensitive); see [Logging](#logging) |

`DEFAULT_MAX_UPLOAD_BYTES` is also exported as a module-level constant.

### Logging

`bioparser/logging_config.py` configures the whole process's log output.
`setup_logging()` puts one handler on the root logger that writes each record to
stderr as one line of JSON:

```json
{"timestamp": "2026-09-22T09:05:56.721+00:00", "level": "INFO", "logger": "bioparser.services.mineru.client", "msg": "mineru-api request completed", "status_code": 200, "bytes_read": 183422, "requests": 5, "latency_ms": 4123.4, "checksum": "9f86d081884c7d65..."}
```

- Fields: `timestamp` (UTC), `level`, `logger`, `msg`, then any fields passed with
  `extra=`, then `exc_type`/`exc_info` when an exception is logged. An `extra`
  field never replaces one of the fixed keys; a `bytes` value is rendered as its
  size (`"<21 bytes>"`), never its contents.
- `BIOPARSER_LOG_LEVEL` sets the level of the `bioparser` loggers only.
  Third-party loggers (httpx2, httpcore2, openai) stay at `WARNING` or stricter,
  because httpx2 logs full URLs at `INFO`.
- uvicorn's loggers keep the level from its `--log-level` and go through the same
  handler, on stderr rather than stdout, from uvicorn's first line: `app.py` sets up
  logging at import too. Only the supervisor process of `--reload` or `--workers`,
  which never imports the app, keeps uvicorn's format. Access lines carry
  `client_addr`, `method`, `path` (without the query string, where clients put
  tokens), `http_version` and `status_code` instead of `msg`. `--no-access-log` is
  respected.
- `log_context(**fields)` adds fields to every record logged inside it, from any
  logger. Access lines do not carry it.

What is logged:

| Logger | Message | Level | Fields |
|---|---|---|---|
| `bioparser.services.mineru.client` | `mineru-api request completed` / `mineru-api request failed` | INFO / WARNING | `status_code`, `bytes_read`, `latency_ms`, then `error` on failure |
| `bioparser.services.vllm.vllm` | `vllm completion finished` / `vllm request failed` | INFO if `finish_reason` is `stop`, else WARNING | `model`, `latency_ms`, `finish_reason`, `prompt_tokens`, `completion_tokens`, or `latency_ms` and `error` |

Never logged: PDF bytes, prompt text, generated text, quotations, the vLLM API
key, or credentials in configured URLs. Exceptions are named by type (`error`,
`cause`). A logged traceback keeps every frame and exception type, but only
exceptions defined in `bioparser` keep their message; any other message becomes
`[message redacted]`, because it can quote its input (a vLLM 400 echoing the
prompt) or a credential (a gateway quoting the rejected API key). So never put
input data in a `bioparser` exception message. The pydantic models, including
`ApiSettings`, also set `hide_input_in_errors=True`.

Reading the logs locally:

```
docker compose logs --no-log-prefix bioparser | jq -R 'fromjson? // .'
```

---

## Out of scope for this draft
 
No `/api/v1` prefix. No request-ID middleware. These are the gaps named in the scope note
above, deferred until a later revision of this document.

## Verification

Start the API in one terminal (this blocks):

```
uv run bioparser
```

Then, from another terminal in the repo root, create a minimal fixture and walk
the happy path:

```
# a minimal valid PDF: only the %PDF- magic and a non-empty body are checked
printf '%%PDF-1.4\n%%%%EOF\n' > sample.pdf

curl -s localhost:8080/health
# -> {"status":"ok"}

curl -s -F file=@sample.pdf localhost:8080/api/extractions
# -> 202  {"job_id":"...","status":"queued"}

curl -s localhost:8080/api/jobs/<job_id>
# -> 200  {"job_id":"...","status":"queued"}
```

Each validation path responds with `{"detail": {"code": ..., "message": ...}}`:

```
# no file part
curl -s -X POST localhost:8080/api/documents
# -> 400  missing_file

# content type is not application/pdf
curl -s -F 'file=@sample.pdf;type=text/plain' localhost:8080/api/documents
# -> 415  unsupported_content_type

# empty body
: > empty.pdf && curl -s -F file=@empty.pdf localhost:8080/api/documents
# -> 400  empty_file

# bytes that are not a PDF (curl still sends type=application/pdf for a .pdf name)
printf 'not a pdf\n' > notpdf.pdf && curl -s -F file=@notpdf.pdf localhost:8080/api/documents
# -> 400  invalid_pdf

# unknown job id
curl -s localhost:8080/api/jobs/does-not-exist
# -> 404  {"detail":{"code":"job_not_found","message":"Job not found"}}
```

The size limit is read from the environment at startup (see [`config.py`](#configpy)),
so exercise it by restarting the server with a small ceiling. The ceiling applies to
the whole HTTP request body (multipart wrapping included), not only the PDF bytes,
so a 10-byte limit rejects even a tiny file:

```
BIOPARSER_MAX_UPLOAD_BYTES=10 uv run bioparser
curl -s -F file=@sample.pdf localhost:8080/api/documents
# -> 413  Content Too Large
```

A done job's ETS body needs a stored extraction artifact. The minimal PDF above stays
`queued` until a worker parses and extracts it.

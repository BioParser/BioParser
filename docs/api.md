# BioParser API contract and module layout

This document defines the public HTTP contract of the API container and the internal
module layout that implements it. The system context for these endpoints is in
[architecture.md](architecture.md).

Status: **draft** — all seven routes (`POST /api/extractions`, `GET /api/jobs/{job_id}`,
`GET /api/jobs/{job_id}/artifacts/parser`, `GET /api/jobs/{job_id}/artifacts/extraction`,
`POST /extract`, `GET /health`, `GET /ready`) are implemented, along with upload validation and a
configurable size limit. `POST /extract` is a dev route: it runs a queued job's pipeline
(the same parser and extractor) in one request, without the queue and the workers.

**Current API scope:**

- **Job state is stored in Postgres.** The API and both workers share the typed,
  versioned `JobState` model through `bioparser.db.jobs`. The `Job` class there
  is the table, and Alembic diffs it when generating a revision. Redis is the queue.
- **The parser worker is a separate process.** `POST /api/extractions` enqueues a
  `ParseJobMessage`; `python -m bioparser.worker.parse` consumes it and advances
  the job from `queued` through `parsing` to `parsed` or `failed`.
- **Uploaded PDFs are persisted in artifact storage.** Their paths are derived
  from the content SHA-256, so two submissions of the same PDF create separate
  jobs that reuse one immutable PDF artifact.
- **`POST /extract` is a temporary dev route** that skips the queue and the workers.
  It runs the same parser and extractor as a queued job, in a child process, and returns
  the extraction artifact inline. Nothing is stored.
- **Readiness (`/ready`) is supported.** The readiness endpoint performs
  lightweight dependency checks only when corresponding environment variables are
  set (see *Readiness behaviour* below).
- **No `/api/v1` prefix or global exception handlers.** Routes raise
  `fastapi.HTTPException` directly and use structured `{"code", "message"}`
  details for the queued-job API's documented failures.

---

## Scope

The API container accepts a born-digital PDF, creates a job, and lets the client poll
for its status. `POST /api/extractions` stores the PDF, creates the Postgres job row, and
enqueues parsing without parsing in the request process. The separate parser worker
advances the stored state. `POST /extract` runs the same upload through the same
pipeline in one request, for manual testing outside the job lifecycle.

---

## HTTP contract

### Authentication

All endpoints require a shared API key, except the explicit public endpoints: `/health`, `/ready`, `/docs`, `/docs/oauth2-redirect`, `/openapi.json`, and `/redoc`.

- **Header:** `X-API-Key: <key>`
- **Configuration:**
  - `BIOPARSER_API_KEY`: The expected secret key (stored as `SecretStr`). Must be at least 32 visible ASCII characters (without spaces). Generate with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`. Required unless `BIOPARSER_AUTH_DISABLED=true`.
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

### `POST /api/extractions`

Submit a PDF.

- **Request:** `multipart/form-data` with a single `file` part.
- **Accepted content type:** `application/pdf` only, checked against the `file`
  part's own `content_type`.
- **Body checks:** the upload must be non-empty and must begin with the `%PDF-`
  magic bytes.
- **Size limit:** the upload is read in 1 MiB chunks and rejected once it exceeds
  `config.MAX_UPLOAD_BYTES` (20 MiB by default — see [`config.py`](#configpy)).
- Submitting the same file twice creates two separate jobs. The PDF artifact is
  content-addressed by SHA-256 and reused by both jobs.

The checks run in a fixed order: `Content-Length` header format and size →
`file` part present → content type → body size while streaming → non-empty body →
`%PDF-` magic. The first one to fail is the one reported.

**Success — `202 Accepted`:**

```json
{
  "job_id": "b3f1c2a4-...",
  "status": "queued"
}
```

**Failure:**

| Status | When |
|--------|------|
| 400 | no `file` part in the request |
| 400 | the file body is empty |
| 400 | the file body does not start with `%PDF-` |
| 400 | the Content-Length header is not a valid non-negative integer |
| 413 | the file exceeds `config.MAX_UPLOAD_BYTES` |
| 415 | content type is not `application/pdf` |
| 503 | artifact storage, the database, or the parse queue is unavailable |


**Validation error body:**

- Size-limit failures (`413`) use Starlette's plain-text body `Content Too Large`
  (same as `RequestBodyLimitMiddleware`).
- Every other validation failure responds with FastAPI's `detail` wrapper:

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


### `GET /api/jobs/{job_id}`

Poll a job.

**Success — `200 OK`:**

```json
{
  "job_id": "b3f1c2a4-...",
  "status": "queued"
}
```

- `status` is one of `"queued"`, `"parsing"`, `"parsed"`, `"extracting"`,
  `"done"`, or `"failed"`. Failed responses include a safe error code.
- The response holds no storage paths. When a job is `done`, #179 adds its ETS response
  here. The UI reads the internal artifacts from the
  [job artifact routes](#get-apijobsjob_idartifactsparser-and-artifactsextraction).

**Failure:**

| Status | When |
|--------|------|
| 404 | no job with that id |
| 422 | `job_id` is not a version 4 UUID |
| 500 | stored job state is unreadable |
| 503 | the database is unavailable |

The route reads only the job state. It never reads an artifact.

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

- The route reads the stored bytes in a thread and does not parse them: the worker
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

### `POST /extract`

Dev route: a queued job's pipeline in one request, without the queue and the workers.
Does not read or write the job store or artifact storage. It will be removed with #179.

- **Request:** identical to `POST /api/extractions` — `multipart/form-data` with a single
  `file` part, subject to the same `Content-Length`, content-type, size, and
  `%PDF-` checks described above.
- Runs [`bioparser.pipeline.run_pipeline()`](#bioparserpipeline): the parser that
  `POST /api/extractions` records on a job (`DEFAULT_PARSER_NAME`), then the job's extractor
  (`DEFAULT_EXTRACTOR_NAME`), built by the same factories and with the same settings as in
  the workers. The response is the extraction artifact that the extractor worker stores,
  after the same check (`bioparser.extract.checks.checked_artifact`). Its format:
  [extraction-artifact.md](extraction-artifact.md).
- **Event loop:** the run happens in a child process (a `ProcessPoolExecutor` with
  `config.extract_concurrency` processes). The API's event loop does not parse the PDF or
  validate a parser artifact, so other routes keep answering during a run.
- **Concurrency:** one slot per process. A request that can't get a slot within
  `config.extract_queue_timeout_seconds` (default 5s) gets `503` with a
  `Retry-After: 30` header instead of queuing. The run frees its slot when it ends, not
  when the request ends: a client that goes away cannot stop a running child.

**Success — `200 OK`:**

```json
{
  "schema_version": "1",
  "extractor": "default",
  "extractor_version": "1",
  "observations": [...]
}
```

An observation is in the response only when the extractor located its fields in the
parser artifact; an observation whose quotation the paper does not contain is dropped.

**Failure:**

| Status | When |
|--------|------|
| 400 / 415 | same upload validation failures as `POST /api/extractions` |
| 502 | a pipeline step failed; `detail` names it: `Parsing failed`, `Parsing timed out`, `The PDF could not be parsed`, `Extraction did not match the schema`, `Extraction failed its checks`, `Extraction truncated`, `Extraction request rejected`, `Extraction backend unavailable`, or `Pipeline process died` |
| 503 | every pipeline process is busy and the queue timeout elapsed |

`502` and `503` bodies use FastAPI's default `{"detail": "<message>"}` shape (a
plain string), not `POST /api/extractions`'s `{"code", "message"}` object.

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

Readiness. Checks Redis, Postgres, and the artifact directory, each with a bounded
timeout, and returns `200 {"status": "ready"}` when all three succeed.

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
- It does the same kind of TCP connect against the Postgres URL (default port 5432),
  with the same timeout. On failure `503` is returned with `"postgres"` in the
  `unavailable` list. The response does not include the URL or its password.
- It attempts to create and remove a temporary file in the artifact directory to confirm
  writability. On failure `503` is returned with `"storage"` in the `unavailable` list.
- Invalid configuration, including an empty required setting, returns `503` with
  `"config"` in the `unavailable` list. The API also refuses to start when these
  settings are missing.

Checks are intentionally minimal: they use a raw TCP connect for Redis and Postgres and a
local file operation for storage so that checks are fast, require no credentials, and do
not expose secrets in error messages.

---

## Module layout

```
src/bioparser/api/
  __init__.py       # main() -> starts the app
  app.py            # FastAPI app, lifespan startup, all seven routes
  auth.py           # API key authentication dependency (X-API-Key)
  config.py         # process configuration read from the environment
  errors.py         # named ErrorDetail constants, codes, and OpenAPI helpers
  middleware.py     # body size limit middleware
  schema.py         # pydantic response/error models used across routes
  uploads.py        # upload-validation helpers shared by POST /api/extractions and POST /extract
```

No `ports/` or `adapters/` folders exist inside `api/` itself. Routes live
directly in `app.py`; upload validation is in `uploads.py`; response/error models
are in `schema.py`. `POST /extract` calls `bioparser.pipeline`, which is outside this package
so that a child process can import it without building the FastAPI app.

### `app.py`

Holds the FastAPI app, its startup/shutdown lifecycle, and all seven route handlers.

**Lifespan:** on startup, first calls `setup_logging(settings.log_level)` (see
[Logging](#logging)), then builds a `VLLMService` (`bioparser.services.vllm`) from
`config.get_api_settings()` and probes the vLLM model id once. The probe is best-effort: a failure logs one WARNING
and startup continues. When configured, the lifecycle also opens
the async Postgres pool (`bioparser.db.engine`, asyncpg), a Redis-backed `DramatiqJobQueue`, and a
`FileSystemArtifactStorage`. For `POST /extract` it creates a `ProcessPoolExecutor` and an
`asyncio.Semaphore`, both sized `config.extract_concurrency`. On shutdown, the pool stops
taking work (a running child is not waited for), and network clients and the Postgres
pool are closed. Migrations stay on the sync psycopg driver.

- `POST /api/extractions` — runs `_validated_upload()` (the [`uploads.py`](#uploadspy) helpers,
  in order: `validate_content_length`, `require_file`, `validate_content_type`,
  `read_upload`, `validate_pdf_content`), persists the content-addressed PDF,
  creates a `JobState` in Postgres, commits it, then enqueues a `ParseJobMessage`,
  and returns `202`. The enqueue is outside the database transaction. A failed
  enqueue marks that row `failed` in a later transaction.
  Starlette's `RequestBodyLimitMiddleware` aborts an oversized body while it is still
  being received. A missing or understated `Content-Length` is still capped while
  reading the file in chunks.
- `GET /api/jobs/{job_id}` — reads the typed state from Postgres and
  maps it to the public status response. One Postgres read; no artifact read.
- `GET /api/jobs/{job_id}/artifacts/parser` and `.../extraction` — read the job, return `409`
  when it has no such ref, then return the stored bytes. The read runs in a thread.
- `POST /extract` — runs the same `_validated_upload()` as `POST /api/extractions`, takes a
  slot (`_start_pipeline()`), and runs [`run_pipeline()`](#bioparserpipeline) in the process
  pool. A `PipelineError` becomes a `502 HTTPException`. If a child died (for example out
  of memory), the broken pool is replaced and the request gets `502`. Not wired to the job store.
- `GET /health` — returns `HealthResponse(status="ok")` directly, no dependencies.
- `GET /ready` — checks Redis, Postgres, and the artifact directory.

Errors use `fastapi.HTTPException` directly — raised from the `uploads.py` helpers for
upload validation, inline in `job_status` for the 404, inline in `extract` for the 502,
and inline in `_start_pipeline` for the 503. A `413` handler maps oversize errors to
Starlette's plain-text `Content Too Large` body.

### `uploads.py`

Stateless helpers that validate an upload, shared by `POST /api/extractions` and
`POST /extract`. Each raises `fastapi.HTTPException` with a `{"code", "message"}`
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

- `JobStatusResponse` — discriminated union for queued, running, successful, and
  failed job states, returned by `GET /api/jobs/{job_id}`. `POST /api/extractions`
  returns `QueuedJobResponse`.
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

### `bioparser.pipeline`

`run_pipeline(pdf, options)`, which `POST /extract` runs in a child process: the parse
worker's parser (`get_parser`), then the extractor worker's extractor (`get_extractor`),
in one blocking call. `PipelineOptions` holds what a job takes from its record and its
workers' settings: parser and extractor names, the parser time limit, the MinerU URL and
tier, and the extraction budgets.

- Raises `PipelineError` with a safe message when a step fails; `app.py` turns that into
  the endpoint's `502`.
- On success, returns the `ExtractionArtifact` that the extractor worker would store, after
  the worker's check (`checked_artifact`).
- Builds its own `VLLMService` for each run, because each run has its own event loop.

### `config.py`

Process configuration via pydantic `BaseSettings` (`ApiSettings`), loaded once
through `get_api_settings()` (cached) and read from a `.env` file plus
`BIOPARSER_`-prefixed environment variables. Fields:

| Field | Default | Meaning |
|-------|---------|---------|
| `max_upload_bytes` | `DEFAULT_MAX_UPLOAD_BYTES` (20 MiB) | upload ceiling used by `uploads.py` and the body-limit middleware |
| `mineru_base_url` | `http://mineru:8000` | MinerU base URL for `POST /extract` when the job parser is `mineru` |
| `mineru_timeout_seconds` | `600.0` | how long the API waits for MinerU to parse one PDF, time queued behind other parses included (its `wait_timeout`, at most 3600); the task is cancelled then |
| `mineru_tier` | `basic` | MinerU parse tier (`basic`, `standard`, `advanced`); the GPU stack sets `standard`, see [parser.md](parser.md) |
| `extract_concurrency` | `2` | number of `POST /extract` processes, and of slots for them |
| `extract_queue_timeout_seconds` | `5.0` | how long `POST /extract` waits for a semaphore slot before returning `503` |
| `dependency_check_timeout_seconds` | `1.0` | timeout for each readiness dependency check |
| `artifact_storage_path` | required | filesystem artifact root |
| `extraction_char_budget` | `8000` | character (not token) budget passed to vLLM extraction |
| `extraction_max_tokens` | `1024` | max generation tokens passed to vLLM extraction |
| `redis_url` | required | Redis URL for the parse queue |
| `redis_timeout_seconds` | `5.0` | Redis connect and operation timeout |
| `database_url` | required | `postgresql` URL (`PostgresDsn`) for job state; `/ready` TCP-connects to it |
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
{"timestamp": "2026-09-22T09:05:56.721+00:00", "level": "INFO", "logger": "bioparser.services.mineru.client", "msg": "mineru-api request completed", "status_code": 200, "bytes_read": 183422, "latency_ms": 4123.4, "checksum": "9f86d081884c7d65..."}
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
  logger. `run_pipeline()` uses it for `checksum` (the upload's sha256), so the
  MinerU, vLLM and stage records of one run share it. Access lines do not carry it.

What is logged:

| Logger | Message | Level | Fields |
|---|---|---|---|
| `bioparser.api.app` | `vllm model discovered` / `vllm model discovery failed` | INFO / WARNING | `vllm_base_url` (credentials, query and fragment removed), then `model`, or `error` and `cause` |
| `bioparser.pipeline` | `pipeline stage completed` / `pipeline stage failed` | INFO / ERROR | `stage` (`parse`, `extract`), `outcome` (`ok` or `error`), `duration_ms`, then results (`pages`; `observations`), or `error`, `cause` and the traceback |
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
 
No PDF persistence in the API routes yet (pending wiring to artifact storage). No
Redis-backed queue worker for `POST /api/extractions`. No worker that advances a
job past `queued`. No `/api/v1` prefix. No idempotency — `POST /api/extractions`
computes a checksum but doesn't use it to detect duplicate uploads. No structured
error envelope for `POST /extract`'s `502`/`503` (they use FastAPI's plain
`{"detail": "<message>"}`, unlike `POST /api/extractions`'s `{"code", "message"}` object). No
request-ID middleware or structured logging (see the `# TODO: logger` markers in
`app.py` and `pipeline.py`). These are the gaps named in the scope note
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
curl -s -X POST localhost:8080/api/extractions
# -> 400  missing_file

# content type is not application/pdf
curl -s -F 'file=@sample.pdf;type=text/plain' localhost:8080/api/extractions
# -> 415  unsupported_content_type

# empty body
: > empty.pdf && curl -s -F file=@empty.pdf localhost:8080/api/extractions
# -> 400  empty_file

# bytes that are not a PDF (curl still sends type=application/pdf for a .pdf name)
printf 'not a pdf\n' > notpdf.pdf && curl -s -F file=@notpdf.pdf localhost:8080/api/extractions
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
curl -s -F file=@sample.pdf localhost:8080/api/extractions
# -> 413  Content Too Large
```

`POST /extract` is not part of the walkthrough above: it needs a reachable MinerU
service (`config.mineru_base_url`) and vLLM service, and expects a real (non-trivial)
PDF rather than the minimal `sample.pdf` fixture, since MinerU has to actually parse
it.

# BioParser API contract and module layout

This document defines the public HTTP contract of the API container and the internal
module layout that implements it. The system context for these endpoints is in
[architecture.md](architecture.md).

Status: **draft** — all five routes (`POST api/extractions`, `GET /api/jobs/{job_id}`,
`POST /extract`, `GET /health`, `GET /ready`) are implemented, along with upload validation and a
configurable size limit. `POST /extract` runs the real MinerU → vLLM pipeline
synchronously for manual testing; it is not wired to the job store. The larger
infrastructure below (Redis, a worker that advances `api/extractions` jobs, artifact
storage) is not implemented.

**Sprint 1 scope:** this is a deliberately reduced first pass, matching
[architecture.md](architecture.md)'s core idea (accept a PDF, return
immediately, poll for status) but not yet its full infrastructure. In
particular:

- **Job store** is an in-process Python dict, not Redis. Jobs do not
  survive a restart and are not visible to any other process.
- **No worker exists yet.** A job created via `POST /api/extractions` is created as
  `queued` and nothing currently advances it to `running` or `succeeded`.
- **`POST /extract` is a separate, synchronous test path**, not the worker above.
  It runs the real MinerU → mapper → vLLM pipeline end-to-end and returns the
  result inline, bounded by a concurrency semaphore, but it does not read or
  write the job store, and `POST /api/extractions` never calls it.
- **No artifact storage.** Uploaded PDF bytes are not persisted anywhere.
- **Readiness (`/ready`) is supported.** The readiness endpoint performs
  lightweight dependency checks only when corresponding environment variables are
  set (see *Readiness behaviour* below).
- **No `/api/v1` prefix, no checksum-based idempotency, no global error envelope or exception handlers** 
- routes still raise plain `fastapi.HTTPException` directly, with no registered `@app.exception_handler`. `POST /api/extractions`'s validation failures do pass a structured `{"code", "message"}` object as `detail` (see above), but that's local to this one route, not a repo-wide convention.

These are the pieces `architecture.md` calls for that this draft does
not yet satisfy. They are the intended next steps, not omissions to be
missed: Redis and a real job store, an artifact storage interface, a
parser worker, and the `ports`/`adapters` split that lets tests swap in
fakes. `POST /extract` already exercises the real MinerU/vLLM pipeline
synchronously, but that's a manual-testing shortcut, not the queued worker
described here. This note should be replaced or removed once those exist.

---

## Scope

The API container accepts a born-digital PDF, creates a job, and lets the client poll
for the job's status. `POST /api/extractions` never parses in the request process — it only
records that a job exists; nothing currently advances that job (see Sprint 1 scope
above). Separately, `POST /extract` runs the same kind of upload through the real
MinerU → vLLM pipeline synchronously, for manually testing the pipeline itself outside
the job lifecycle.

---

## HTTP contract

### `POST /api/extractions`

Submit a PDF.

- **Request:** `multipart/form-data` with a single `file` part.
- **Accepted content type:** `application/pdf` only, checked against the `file`
  part's own `content_type`.
- **Body checks:** the upload must be non-empty and must begin with the `%PDF-`
  magic bytes.
- **Size limit:** the upload is read in 1 MiB chunks and rejected once it exceeds
  `config.MAX_UPLOAD_BYTES` (20 MiB by default — see [`config.py`](#configpy)).
- No idempotency — submitting the same file twice creates two separate jobs. A
  sha256 checksum is computed from the upload for `POST /extract`'s use, but
  `POST /api/extractions` discards it without checking for an existing job.

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

- `status` is `"queued"` for every job right now — nothing exists yet to move it to
  `"running"`, `"succeeded"`, or `"failed"`. Those states are reserved for when a
  worker is added.

**Failure:**

| Status | When |
|--------|------|
| 404 | no job with that id |

**Error body:**

```json
{
  "detail": {
    "code": "job_not_found",
    "message": "Job not found"
  }
}
```

### `POST /extract`

Runs the full parse-and-extract pipeline synchronously, for manual testing of the
pipeline outside the job lifecycle. Does not read or write the job store.

- **Request:** identical to `POST /api/extractions` — `multipart/form-data` with a single
  `file` part, subject to the same `Content-Length`, content-type, size, and
  `%PDF-` checks described above.
- Runs, in order: MinerU parse (`bioparser.services.mineru.MinerUClient`) →
  `artifact_from_middle_json` mapping → vLLM extraction
  (`bioparser.services.vllm.VLLMService`) — see [`pipeline.py`](#pipelinepy).
- **Concurrency:** bounded by `config.extract_concurrency` (default 2) via an
  `asyncio.Semaphore` created at startup. A request that can't acquire a slot
  within `config.extract_queue_timeout_seconds` (default 5s) gets `503` with a
  `Retry-After: 30` header instead of queuing indefinitely.

**Success — `200 OK`:**

```json
{
  "checksum": "...",
  "pages": 12,
  "blocks_sent": 340,
  "prompt_characters": 7421,
  "truncated": false,
  "ungrounded_observations": 0,
  "unit_mismatched_observations": 0,
  "observations": [...]
}
```

**Failure:**

| Status | When |
|--------|------|
| 400 / 415 | same upload validation failures as `POST /api/extractions` |
| 502 | MinerU parsing failed, the parser output could not be mapped, or vLLM extraction failed, was truncated, or didn't match the schema — see [`pipeline.py`](#pipelinepy) |
| 503 | the extraction semaphore is saturated and the queue timeout elapsed |

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

### `GET /ready`

Readiness. Performs optional, bounded dependency checks and returns `200 {"status": "ready"}`
when the process can accept work. Behaviour is opt-in via environment variables so the
default developer experience remains simple.

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

- If `BIOPARSER_REDIS_URL` is set, the endpoint parses the Redis URL and attempts a TCP
  connection to the host/port from that URL with a per-check timeout controlled by
  `BIOPARSER_DEPENDENCY_CHECK_TIMEOUT_SECONDS` (seconds, default `1.0`). On failure the
  endpoint returns `503`; FastAPI places the readiness payload under `detail`, e.g.
  `{"detail": {"status": "not ready", "unavailable": ["redis"]}}`.
- If `BIOPARSER_ARTIFACT_STORAGE_PATH` is set, the endpoint attempts to create and remove
  a temporary file in that directory to confirm writability. On failure `503` is returned
  with `"storage"` in the `unavailable` list.
- If neither environment variable is set the endpoint returns `200`.

Checks are intentionally minimal: they use a raw TCP connect for Redis and a local
file operation for storage so that checks are fast, require no credentials, and do not
expose secrets in error messages.

---

## Module layout

```
src/bioparser/api/
  __init__.py       # main() -> starts the app
  app.py            # FastAPI app, lifespan startup, all four routes
  uploads.py        # upload-validation helpers shared by POST /api/extractions and POST /extract
  schema.py         # pydantic response/error models used across routes
  pipeline.py       # run_pipeline(): MinerU -> mapper -> vLLM, used by POST /extract
  config.py         # process configuration read from the environment
  jobs.py           # an in-process dict acting as the job store, plus a helper
                     #   to create/read jobs
```

No `ports/`, `adapters/`, or `ets/` folders yet inside `api/` itself. Routes live
directly in `app.py`; upload validation is in `uploads.py`; response/error models
are in `schema.py`. `pipeline.py` (used only by `POST /extract`) reaches outside
this package into `bioparser.services.mineru`, `bioparser.services.vllm`,
`bioparser.parser`, and `bioparser.extract` — those are documented in
[architecture.md](architecture.md), not here.

### `app.py`

Holds the FastAPI app, its startup/shutdown lifecycle, and all four route handlers.

**Lifespan:** on startup, first calls `setup_logging(settings.log_level)` (see
[Logging](#logging)), then builds a `MinerUClient` (`bioparser.services.mineru`) and
a `VLLMService` (`bioparser.services.vllm`) from `config.get_api_settings()`, and
probes the vLLM model id once. The probe is best-effort: a failure logs one WARNING
and startup continues. It also creates an `asyncio.Semaphore` sized `config.extract_concurrency`
on `app.state.extract_sem`, used only by `POST /extract`. On shutdown, both clients
are closed.

- `POST /api/extractions` — runs `_validated_upload()` (the [`uploads.py`](#uploadspy) helpers,
  in order: `validate_content_length`, `require_file`, `validate_content_type`,
  `read_upload`, `validate_pdf_content`), then calls `jobs.create_job()` and returns
  `202`. `_validated_upload()` also computes a sha256 checksum of the body, but
  `POST /api/extractions` discards both the checksum and the bytes — nothing is persisted.
  Starlette's `RequestBodyLimitMiddleware` aborts an oversized body while it is still
  being received. A missing or understated `Content-Length` is still capped while
  reading the file in chunks.
- `GET /api/jobs/{job_id}` — calls `jobs.get_job(job_id)`, returns it or raises a 404
  `HTTPException`.
- `POST /extract` — runs the same `_validated_upload()` as `POST /api/extractions`, then, inside
  an `_extract_slot()` (the concurrency semaphore), calls
  [`pipeline.run_pipeline()`](#pipelinepy) and returns its result as-is. A
  `PipelineError` is re-raised as a `502 HTTPException`. Not wired to the job store.
- `GET /health` — returns `HealthResponse(status="ok")` directly, no dependencies.
- `GET /ready` — optional dependency checks controlled by environment variables.

Errors use `fastapi.HTTPException` directly — raised from the `uploads.py` helpers for
upload validation, inline in `job_status` for the 404, inline in `extract` for the 502,
and inline in `_extract_slot` for the 503. A `413` handler maps oversize errors to
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

- `JobStatusResponse` — `{job_id: str, status: Literal["queued"]}`, returned by
  `POST /api/extractions` and `GET /api/jobs/{job_id}`.
- `HealthResponse` — `{status: Literal["ok"]}`, returned by `GET /health`.

### `errors.py`

Error types and the named `ErrorDetail` instances used across `uploads.py` and `app.py`.

- `ErrorCode` — the fixed set of error codes from the `POST /api/extractions` table above,
  plus `job_not_found`.
- `ErrorDetail` — `{code: ErrorCode, message: str}`, the shape every `uploads.py`
  helper and the 404 handler raise as `HTTPException.detail`.
- `ErrorResponse` — `{detail: ErrorDetail}`, used only in `responses=` schemas for
  OpenAPI documentation; it is never constructed at runtime.
- `MISSING_FILE`, `EMPTY_FILE`, `INVALID_PDF`, `INVALID_CONTENT_LENGTH`,
  `UNSUPPORTED_CONTENT_TYPE`, `JOB_NOT_FOUND` — one named `ErrorDetail` per cause,
  the single source of truth for each error's `code`/`message` pair.
- `http_error(status_code, detail)` — builds the `HTTPException` raised at each
  failure site from one of the constants above.
- `error_response(*details)` — builds the `responses=` OpenAPI entry (schema +
  named examples) for a route decorator from the same constants.

### `pipeline.py`

`run_pipeline()`, the function `POST /extract` calls: MinerU parse →
`artifact_from_middle_json` mapping → vLLM extraction, all in one async call.

- Raises `PipelineError` — wrapping the underlying `MinerUError`, the mapper's
  `ValueError`, `VLLMTruncatedError`, `ExtractionFailedError`, or `VLLMError` — on
  any stage failure; `app.py` turns that into the endpoint's `502`.
- On success, returns a plain `dict` with `checksum`, `pages`, `blocks_sent`,
  `prompt_characters`, `truncated`, `ungrounded_observations`,
  `unit_mismatched_observations`, and `observations` (each observation's
  `.payload()`).
- The only module in `api/` that reaches into `bioparser.parser` and
  `bioparser.extract`; the MinerU/vLLM clients themselves live in
  `bioparser.services` and are only constructed in `app.py`'s lifespan.

### `config.py`

Process configuration via pydantic `BaseSettings` (`ApiSettings`), loaded once
through `get_api_settings()` (cached) and read from a `.env` file plus
`BIOPARSER_`-prefixed environment variables. Fields:

| Field | Default | Meaning |
|-------|---------|---------|
| `max_upload_bytes` | `DEFAULT_MAX_UPLOAD_BYTES` (20 MiB) | upload ceiling used by `uploads.py` and the body-limit middleware |
| `mineru_base_url` | `http://mineru:8000` | base URL for `MinerUClient` |
| `mineru_timeout_seconds` | `600.0` | MinerU request timeout |
| `mineru_connect_timeout_seconds` | `5.0` | MinerU connect timeout |
| `extract_concurrency` | `2` | size of the `POST /extract` semaphore |
| `extract_queue_timeout_seconds` | `5.0` | how long `POST /extract` waits for a semaphore slot before returning `503` |
| `extraction_char_budget` | `8000` | character (not token) budget passed to vLLM extraction |
| `extraction_max_tokens` | `1024` | max generation tokens passed to vLLM extraction |
| `log_level` | `INFO` | level of the `bioparser` loggers (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`; case-insensitive); see [Logging](#logging) |

`DEFAULT_MAX_UPLOAD_BYTES` is also exported as a module-level constant.

### `jobs.py`

A plain Python dict mapping `job_id -> JobRecord` (a `@dataclass` with `job_id` and
`status`), plus two small functions:

- `create_job() -> JobRecord` — generates a `job_id` via `uuid4()`, stores it as
  `queued`, returns the record.
- `get_job(job_id: str) -> JobRecord | None` — looks up the record, or `None` if
  missing.

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
| `bioparser.api.pipeline` | `pipeline stage completed` / `pipeline stage failed` | INFO / ERROR | `stage` (`parse`, `map`, `extract`), `outcome` (`ok` or `error`), `duration_ms`, then results (`pages`; `observations`, `blocks_sent`, `truncated`, ...), or `error`, `cause` and the traceback |
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

No PDF storage. No Redis-backed queue for `POST /api/extractions`. No worker that advances a
job past `queued`. No `/api/v1` prefix. No idempotency — `POST /api/extractions`
computes a checksum but doesn't use it to detect duplicate uploads. No structured
error envelope for `POST /extract`'s `502`/`503` (they use FastAPI's plain
`{"detail": "<message>"}`, unlike `POST /api/extractions`'s `{"code", "message"}` object). No
request-ID middleware, so access lines are not correlated with a pipeline run's
records. These are the gaps named in the Sprint 0 scope note
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

# BioParser API contract and module layout

This document defines the public HTTP contract of the API container and the internal
module layout that implements it. The system context for these endpoints is in
[architecture.md](architecture.md).

Status: **draft** — all three routes (`POST /submit`, `GET /jobs/{job_id}`,
`GET /health`) are implemented, along with upload validation and a configurable
size limit. The larger infrastructure below (Redis, a worker, artifact storage,
`/ready`) is not.

**Sprint 0 scope:** this is a deliberately reduced first pass, matching
[architecture.md](architecture.md)'s core idea (accept a PDF, return
immediately, poll for status) but not yet its full infrastructure. In
particular:

- **Job store** is an in-process Python dict, not Redis. Jobs do not
  survive a restart and are not visible to any other process.
- **No worker exists yet.** A job is created as `queued` and nothing
  currently advances it to `running` or `succeeded`.
- **No artifact storage.** Uploaded PDF bytes are not persisted anywhere.
- **`/ready` is not implemented.** Only `/health` exists, since there
  are no external dependencies yet to check readiness against.
- **No `/api/v1` prefix, no checksum-based idempotency, no global error envelope or exception handlers** 
- routes still raise plain `fastapi.HTTPException` directly, with no registered `@app.exception_handler`. `POST /submit`'s validation failures do pass a structured `{"code", "message"}` object as `detail` (see above), but that's local to this one route, not a repo-wide convention.

These are the pieces `architecture.md` calls for that this draft does
not yet satisfy. They are the intended next steps, not omissions to be
missed: Redis and a real job store, an artifact storage interface, a
parser worker, and the `ports`/`adapters` split that lets tests swap in
fakes. This note should be replaced or removed once those exist.

---

## Scope

The API container accepts a born-digital PDF, creates a job, and lets the client poll
for the job's status. Parsing never runs in the request process — submitting a PDF only
records that a job exists; nothing currently does the parsing work (see Sprint 0 scope
above).

---

## HTTP contract

### `POST /submit`

Submit a PDF.

- **Request:** `multipart/form-data` with a single `file` part.
- **Accepted content type:** `application/pdf` only, checked against the `file`
  part's own `content_type`.
- **Body checks:** the upload must be non-empty and must begin with the `%PDF-`
  magic bytes.
- **Size limit:** the upload is read in 1 MiB chunks and rejected once it exceeds
  `config.MAX_UPLOAD_BYTES` (20 MiB by default — see [`config.py`](#configpy)).
- No idempotency — submitting the same file twice creates two separate jobs.

The checks run in a fixed order: `file` part present → content type → size limit
(enforced while streaming) → non-empty body → `%PDF-` magic. The first one to
fail is the one reported.

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
| 413 | the file exceeds `config.MAX_UPLOAD_BYTES` |
| 415 | content type is not `application/pdf` |


**Validation error body:**

- Every failure above responds with the same shape, using FastAPI's standard `detail` wrapper:

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
| `file_too_large` | 413 | file exceeds `config.MAX_UPLOAD_BYTES` |
| `unsupported_content_type` | 415 | content type is not `application/pdf` |


### `GET /jobs/{job_id}`

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

### `GET /health`

Liveness. Returns `200 {"status": "ok"}` whenever the process is running. No
dependencies to check yet.

---

## Module layout

```
src/bioparser/
  __init__.py       # main() -> starts the app
  app.py            # creates the FastAPI app, defines all three routes directly
  uploads.py        # upload-validation helpers used by POST /submit
  config.py         # process configuration read from the environment
  jobs.py           # an in-process dict acting as the job store, plus a helper
                     #   to create/read jobs
```

No `api/`, `schemas/`, `ports/`, `adapters/`, `services/`, or `ets/` folders yet. Routes
live directly in `app.py`; only the `POST /submit` input validation has been pulled out
into `uploads.py`. That fuller split is a good next step once there's enough code to
make separate files worth navigating — not before.

### `app.py`

Holds the FastAPI app and all three route handlers:

- `POST /submit` — runs the [`uploads.py`](#uploadspy) helpers in order
  (`require_file`, `validate_content_type`, `read_upload`, `validate_pdf_content`),
  then calls `jobs.create_job()` and returns `202`. The read body is only used for
  validation and is then discarded — nothing is persisted.
- `GET /jobs/{job_id}` — calls `jobs.get_job(job_id)`, returns it or raises a 404
  `HTTPException`.
- `GET /health` — returns `{"status": "ok"}` directly, no dependencies.

Errors use `fastapi.HTTPException` directly — raised from the `uploads.py` helpers
for `POST /submit` and inline in the route for the 404 — with no custom exception
classes, no shared error envelope, and no registered exception handlers.

### `uploads.py`

Stateless helpers that validate a `POST /submit` upload. Each raises
`fastapi.HTTPException` with a `{"code", "message"}` object as its `detail` on
failure, and returns normally on success.

- `require_file(file) -> UploadFile` — raises `400 missing_file` when no `file`
  part was sent; otherwise returns it.
- `validate_content_type(file) -> None` — raises `415 unsupported_content_type`
  unless `file.content_type` is exactly `application/pdf`.
- `read_upload(file) -> bytes` — reads the upload in 1 MiB chunks, raising
  `413 file_too_large` as soon as the running total exceeds
  `config.MAX_UPLOAD_BYTES`; returns the full body otherwise.
- `validate_pdf_content(content) -> None` — raises `400 empty_file` for an empty
  body, and `400 invalid_pdf` if the body does not start with the `%PDF-` magic
  bytes.

### `config.py`

Process configuration, read from the environment once at import time.

- `DEFAULT_MAX_UPLOAD_BYTES` — 20 MiB, the built-in default.
- `MAX_UPLOAD_BYTES` — the effective upload ceiling: the
  `BIOPARSER_MAX_UPLOAD_BYTES` environment variable (interpreted as an integer
  number of bytes) when set, otherwise `DEFAULT_MAX_UPLOAD_BYTES`.

### `jobs.py`

A plain Python dict mapping `job_id -> {"status": "queued"}`, plus two small functions:

- `create_job() -> dict` — generates a `job_id` (e.g. via `uuid4`), stores it as
  `queued`, returns the record.
- `get_job(job_id: str) -> dict | None` — looks up the record, or `None` if missing.

---

## Out of scope for this draft

No PDF storage. No Redis. No worker. No `/ready`. No `/api/v1` prefix. No checksum or
idempotency. No structured error envelope. No request-ID middleware or structured
logging. These are the gaps named in the Sprint 0 scope note above, deferred until a
later revision of this document.

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

curl -s localhost:8000/health
# -> {"status":"ok"}

curl -s -F file=@sample.pdf localhost:8000/submit
# -> 202  {"job_id":"...","status":"queued"}

curl -s localhost:8000/jobs/<job_id>
# -> 200  {"job_id":"...","status":"queued"}
```

Each validation path responds with `{"detail": {"code": ..., "message": ...}}`:

```
# no file part
curl -s -X POST localhost:8000/submit
# -> 400  missing_file

# content type is not application/pdf
curl -s -F 'file=@sample.pdf;type=text/plain' localhost:8000/submit
# -> 415  unsupported_content_type

# empty body
: > empty.pdf && curl -s -F file=@empty.pdf localhost:8000/submit
# -> 400  empty_file

# bytes that are not a PDF (curl still sends type=application/pdf for a .pdf name)
printf 'not a pdf\n' > notpdf.pdf && curl -s -F file=@notpdf.pdf localhost:8000/submit
# -> 400  invalid_pdf

# unknown job id
curl -s localhost:8000/jobs/does-not-exist
# -> 404  {"detail":"Job not found"}
```

The size limit is read from the environment at startup (see [`config.py`](#configpy)),
so exercise it by restarting the server with a small ceiling:

```
BIOPARSER_MAX_UPLOAD_BYTES=10 uv run bioparser
curl -s -F file=@sample.pdf localhost:8000/submit
# -> 413  file_too_large
```

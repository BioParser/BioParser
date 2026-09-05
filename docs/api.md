# BioParser API contract and module layout

This document defines the public HTTP contract of the API container and the internal
module layout that implements it. The system context for these endpoints is in
[architecture.md](architecture.md).

Status: **draft** — nothing here is implemented yet beyond `GET /health`.

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
- **No `/api/v1` prefix, no checksum-based idempotency, no error
  envelope** — routes use plain `HTTPException` for now.

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
- **Accepted content type:** `application/pdf` only, checked via the request's
  content type. (No magic-byte sniff, no streamed size cap yet.)
- No idempotency — submitting the same file twice creates two separate jobs.

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
| 415 | content type is not `application/pdf` |

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
  jobs.py           # an in-process dict acting as the job store, plus a helper
                     #   to create/read jobs
```

No `api/`, `schemas/`, `ports/`, `adapters/`, `services/`, or `ets/` folders yet. Routes
live directly in `app.py`. That split is a good next step once there's enough code to
make separate files worth navigating — not before.

### `app.py`

Holds the FastAPI app and all three route handlers:

- `POST /submit` — checks content type, calls `jobs.create_job()`, returns `202`.
- `GET /jobs/{job_id}` — calls `jobs.get_job(job_id)`, returns it or raises a 404
  `HTTPException`.
- `GET /health` — returns `{"status": "ok"}` directly, no dependencies.

Errors use `fastapi.HTTPException` directly in each route — no custom exception
classes, no shared error envelope, no registered exception handlers.

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

- `uv run bioparser` starts; `curl localhost:8000/health` -> `{"status":"ok"}`.
- `curl -F file=@sample.pdf localhost:8000/submit` -> `202` + `job_id`.
- `curl localhost:8000/jobs/<job_id>` -> `{"job_id": ..., "status": "queued"}`.
- Submitting a non-PDF file -> `415`.
- Polling an unknown `job_id` -> `404`.

import { type DragEvent, type FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import {
  ApiError,
  type CreateJobRequest,
  getUploadLimits,
  startDocumentJob,
  uploadDocument,
} from "../api/client";
import { checkPdf, DEFAULT_MAX_UPLOAD_BYTES, formatMiB, tooLarge } from "../upload/checkPdf";

// The parsers a curator can choose from, with their labels. The keys come from the API's
// schema (ParserName in src/bioparser/parser_names.py), so tsc fails when a name is added,
// removed or renamed there and not here.
type ParserName = CreateJobRequest["parser"];
const PARSER_LABELS: Record<ParserName, string> = { mineru: "MinerU", default: "Default" };
// Object.entries types its keys as string; they are exactly the ParserName keys above.
const PARSERS = Object.entries(PARSER_LABELS) as [ParserName, string][];

// Fixed for now: there is one extractor, and it looks for these traits
// (DEFAULT_EXTRACTOR_NAME in src/bioparser/extractor_names.py, Trait in
// src/bioparser/extract/backend/default/schema.py). Choosing traits is #32.
const EXTRACTOR = { name: "default", label: "Default" };
const TRAITS = ["Body mass", "Body length", "Tail length"];

// The most files that can wait in the list to be sent. The API has no such limit: this only
// keeps an accidental drop of a whole folder from starting a job for every file in it.
export const MAX_FILES_PER_BATCH = 10;

// What happened to one file. A row without a result has not been sent yet.
type FileResult =
  | { state: "rejected"; reason: string }
  | { state: "sending" }
  | { state: "started"; jobId: string }
  | { state: "failed"; reason: string; status?: number };

type Row = { id: number; file: File; title: string; result: FileResult | null };

// Row ids only have to be unique on this page, for React's keys.
let nextRowId = 0;

// The same file picked twice: the browser gives no path, so name, size and change time.
function fileKey(file: File): string {
  return `${file.name}\u0000${file.size}\u0000${file.lastModified}`;
}

// A file in the list blocks the same file from being added again while it waits or after it
// started a job. One whose upload or start failed can be added again, to try once more.
function blocksDuplicate(row: Row): boolean {
  return row.result === null || row.result.state !== "failed";
}

function fileCount(count: number): string {
  return count === 1 ? "1 file" : `${count} files`;
}

export function StartJobPage() {
  const [rows, setRows] = useState<Row[]>([]);
  const [parser, setParser] = useState<ParserName>("default");
  const [sending, setSending] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [maxBytes, setMaxBytes] = useState(DEFAULT_MAX_UPLOAD_BYTES);
  // Why some of the files just picked or dropped were not added, or null.
  const [notice, setNotice] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const batch = useRef<AbortController | null>(null);
  // Each unsent row's Remove button, so focus can move on when a row goes away.
  const removeButtons = useRef(new Map<number, HTMLButtonElement>());
  // After a removal: the row whose button gets focus, or null for the file input.
  const focusAfterRemove = useRef<number | null | undefined>(undefined);

  // Leaving the page (a nav link, or the key screen after a 401) stops the batch, so no
  // upload or job starts that the curator cannot see.
  useEffect(() => () => batch.current?.abort(), []);

  // The limit the API runs with. Until it answers, or if it fails, the default is used;
  // the API still enforces its own limit either way.
  useEffect(() => {
    let current = true;
    getUploadLimits()
      .then((limits) => {
        if (current) {
          setMaxBytes(limits.max_upload_bytes);
        }
      })
      .catch(() => {});
    return () => {
      current = false;
    };
  }, []);

  // Only the drop zone takes files. A drop anywhere else must not open the PDF and leave
  // the app, which would also stop the batch.
  useEffect(() => {
    const block = (event: Event) => event.preventDefault();
    window.addEventListener("dragover", block);
    window.addEventListener("drop", block);
    return () => {
      window.removeEventListener("dragover", block);
      window.removeEventListener("drop", block);
    };
  }, []);

  // Keyboard focus was on a Remove button that is gone: it moves to the next row's button,
  // or to the file input when no unsent row is left.
  // biome-ignore lint/correctness/useExhaustiveDependencies: runs after each change to the rows.
  useEffect(() => {
    const target = focusAfterRemove.current;
    if (target === undefined) {
      return;
    }
    focusAfterRemove.current = undefined;
    const button = target === null ? undefined : removeButtons.current.get(target);
    (button ?? inputRef.current)?.focus();
  }, [rows]);

  // Adds the files to the end of the list. Rows already there keep their place and result.
  function addFiles(files: File[]) {
    if (files.length === 0) {
      return;
    }
    const taken = new Set(rows.filter(blocksDuplicate).map((row) => fileKey(row.file)));
    let room = MAX_FILES_PER_BATCH - rows.filter((row) => row.result === null).length;
    const added: Row[] = [];
    const duplicates: string[] = [];
    let overCap = 0;
    for (const file of files) {
      const key = fileKey(file);
      if (taken.has(key)) {
        duplicates.push(file.name);
      } else if (room <= 0) {
        overCap++;
      } else {
        taken.add(key);
        room--;
        added.push({ id: nextRowId++, file, title: "", result: null });
      }
    }
    setRows([...rows, ...added]);
    const reasons: string[] = [];
    if (duplicates.length === 1) {
      reasons.push(`${duplicates[0]} is already in the list.`);
    } else if (duplicates.length > 1) {
      reasons.push(`${fileCount(duplicates.length)} are already in the list.`);
    }
    if (overCap > 0) {
      reasons.push(
        `${fileCount(overCap)} not added: up to ${MAX_FILES_PER_BATCH} PDFs can wait to be sent.`,
      );
    }
    setNotice(reasons.length > 0 ? reasons.join(" ") : null);
  }

  function removeRow(id: number) {
    const unsent = rows.filter((row) => row.result === null);
    const index = unsent.findIndex((row) => row.id === id);
    const next = unsent[index + 1] ?? unsent[index - 1];
    focusAfterRemove.current = next?.id ?? null;
    setRows(rows.filter((row) => row.id !== id));
    setNotice(null);
  }

  function clearRows() {
    setRows([]);
    setNotice(null);
  }

  function setResult(id: number, result: FileResult) {
    setRows((current) => current.map((row) => (row.id === id ? { ...row, result } : row)));
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    // Without this, the browser opens the dropped file instead of handing it to the page.
    event.preventDefault();
    setDragging(true);
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    // Moving over a child of the drop zone also fires dragleave on the zone.
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setDragging(false);
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (sending) {
      return;
    }
    addFiles(Array.from(event.dataTransfer.files));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const controller = new AbortController();
    batch.current = controller;
    setSending(true);
    setNotice(null);
    try {
      // One file at a time: the API reads each upload into memory, and the results then show
      // in the order of the list.
      for (const row of rows) {
        if (controller.signal.aborted) {
          break;
        }
        if (row.result !== null) {
          continue;
        }
        setResult(row.id, { state: "sending" });
        const result = await processFile(row.file, row.title, parser, maxBytes, controller.signal);
        setResult(row.id, result);
        // The key was refused: every other file would get a 401 too.
        if (result.state === "failed" && result.status === 401) {
          break;
        }
      }
    } finally {
      // Always release the form, whatever happened to the files.
      setSending(false);
    }
  }

  const unsent = rows.some((row) => row.result === null);
  const done = rows.length > 0 && !sending && !unsent;
  const started = rows.filter((row) => row.result?.state === "started").length;

  return (
    <>
      <h1>Start a job</h1>
      <p>Each PDF becomes its own job.</p>
      <form id="start-job-form" onSubmit={handleSubmit} className="job-form">
        <fieldset disabled={sending} className="job-fieldset">
          {/* biome-ignore lint/a11y/noStaticElementInteractions: dropping is a shortcut; the file input inside does the same with a keyboard. */}
          <div
            className={dragging ? "job-drop job-drop-active" : "job-drop"}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <label className="job-field">
              Add PDF files
              <input
                ref={inputRef}
                type="file"
                name="files"
                multiple
                accept="application/pdf,.pdf"
                onChange={(event) => {
                  const input = event.currentTarget;
                  // Copied first: emptying the input also empties its FileList.
                  addFiles(Array.from(input.files ?? []));
                  // The list below shows what was added. An empty input does not name only
                  // the last pick, and picking a removed file again still fires onChange.
                  input.value = "";
                }}
              />
            </label>
            <p className="job-drop-hint">
              or drop them here. Up to {MAX_FILES_PER_BATCH} PDFs, {formatMiB(maxBytes)} each.
            </p>
          </div>
          <fieldset className="job-parsers">
            <legend>Parser</legend>
            {PARSERS.map(([name, label]) => (
              <label key={name}>
                <input
                  type="radio"
                  name="parser"
                  value={name}
                  checked={parser === name}
                  onChange={() => setParser(name)}
                />{" "}
                {label}
              </label>
            ))}
          </fieldset>
        </fieldset>
        <section aria-labelledby="job-summary-heading" className="job-summary">
          <h2 id="job-summary-heading">Summary</h2>
          <dl>
            <dt>Parser</dt>
            <dd>{PARSER_LABELS[parser]}</dd>
            <dt>Extractor</dt>
            <dd>{EXTRACTOR.label}</dd>
            <dt>Traits</dt>
            <dd>{TRAITS.join(", ")}</dd>
          </dl>
        </section>
        <div className="job-actions">
          <button type="submit" disabled={sending || !unsent}>
            {sending ? "Starting jobs…" : "Start jobs"}
          </button>
          <button type="button" onClick={clearRows} disabled={sending || rows.length === 0}>
            Clear list
          </button>
        </div>
      </form>
      {/* Not a status region: the batch summary below is the page's status. */}
      <p className="job-notice" aria-live="polite">
        {notice}
      </p>
      {rows.length > 0 && (
        <ul aria-label="Files" className="job-files">
          {rows.map((row) => (
            <li key={row.id} className="job-file">
              <span className="job-file-name">{row.file.name}</span>
              <label className="job-file-title">
                Title (optional)
                <input
                  type="text"
                  form="start-job-form"
                  aria-label={`Title for ${row.file.name}`}
                  maxLength={500}
                  value={row.title}
                  disabled={sending || row.result !== null}
                  onChange={(event) => {
                    const title = event.currentTarget.value;
                    setRows((current) =>
                      current.map((item) => (item.id === row.id ? { ...item, title } : item)),
                    );
                  }}
                />
              </label>
              <FileStatus result={row.result} />
              {row.result === null && (
                <button
                  type="button"
                  className="job-file-remove"
                  aria-label={`Remove ${row.file.name}`}
                  disabled={sending}
                  onClick={() => removeRow(row.id)}
                  ref={(button) => {
                    if (button === null) {
                      removeButtons.current.delete(row.id);
                    } else {
                      removeButtons.current.set(row.id, button);
                    }
                  }}
                >
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {/* Always on the page, so a screen reader announces the text when it appears. */}
      <p role="status">{done && `${started} of ${rows.length} files started a job.`}</p>
    </>
  );
}

function FileStatus({ result }: { result: FileResult | null }) {
  if (result === null) {
    return <span className="job-file-status">Not sent yet</span>;
  }
  switch (result.state) {
    case "sending":
      return <span className="job-file-status">Sending…</span>;
    case "started":
      return (
        <span className="job-file-status">
          Job <Link to={`/jobs/${encodeURIComponent(result.jobId)}`}>{result.jobId}</Link>
        </span>
      );
    case "rejected":
    case "failed":
      return <span className="job-file-status job-file-error">{result.reason}</span>;
  }
}

// Checks the file, uploads it, then starts its job. Never throws: every failure, the file
// check included, becomes a result to show, so one file cannot stop the batch or lock the form.
async function processFile(
  file: File,
  title: string,
  parser: ParserName,
  maxBytes: number,
  signal: AbortSignal,
): Promise<FileResult> {
  try {
    // Rejects with NotReadableError when the file changed on disk after it was picked.
    const reason = await checkPdf(file, maxBytes);
    if (reason !== null) {
      return { state: "rejected", reason };
    }
    const document = await uploadDocument(file, title, signal);
    const job = await startDocumentJob(
      document.checksum,
      { parser, extractor: EXTRACTOR.name },
      signal,
    );
    return { state: "started", jobId: job.job_id };
  } catch (error) {
    const status = error instanceof ApiError ? error.status : undefined;
    return { state: "failed", reason: failureReason(error, maxBytes), status };
  }
}

function failureReason(error: unknown, maxBytes: number): string {
  if (error instanceof ApiError) {
    // A 413 can come from a proxy in front of the API, with a plain-text body and no message.
    return error.status === 413 ? tooLarge(maxBytes) : error.message;
  }
  if (error instanceof DOMException && error.name === "AbortError") {
    return "Stopped.";
  }
  if (error instanceof DOMException) {
    // NotReadableError or NotFoundError: the file changed or was removed after it was picked.
    return "The file cannot be read. Pick it again.";
  }
  // fetch rejects with a TypeError when the request never got an answer.
  return "The API could not be reached. Try again.";
}

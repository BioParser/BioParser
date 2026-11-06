import { type DragEvent, type FormEvent, useRef, useState } from "react";
import { Link } from "react-router";
import { ApiError, startDocumentJob, uploadDocument } from "../api/client";
import { checkPdf, TOO_LARGE } from "../upload/checkPdf";

// The parsers a curator can choose from (PARSER_BACKENDS in src/bioparser/parser_names.py).
const PARSERS = [
  { name: "mineru", label: "MinerU" },
  { name: "default", label: "Default" },
] as const;
type ParserName = (typeof PARSERS)[number]["name"];

// Fixed for now: there is one extractor, and it looks for these traits
// (DEFAULT_EXTRACTOR_NAME in src/bioparser/extractor_names.py, Trait in
// src/bioparser/extract/backend/default/schema.py). Choosing traits is #32.
const EXTRACTOR = { name: "default", label: "Default" };
const TRAITS = ["Body mass", "Body length", "Tail length"];

// What happened to one file. A row without a result has not been sent yet.
type FileResult =
  | { state: "rejected"; reason: string }
  | { state: "sending" }
  | { state: "started"; jobId: string }
  | { state: "failed"; reason: string };

type Row = { id: number; file: File; result: FileResult | null };

// Row ids only have to be unique on this page, for React's keys.
let nextRowId = 0;

export function StartJobPage() {
  const [rows, setRows] = useState<Row[]>([]);
  const [parser, setParser] = useState<ParserName>("default");
  const [sending, setSending] = useState(false);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function chooseFiles(files: FileList | null) {
    if (files === null || files.length === 0) {
      return;
    }
    setRows(Array.from(files, (file) => ({ id: nextRowId++, file, result: null })));
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
    const files = event.dataTransfer.files;
    if (files.length === 0) {
      return;
    }
    // The list below now shows the dropped files. Empty the file input, so it no longer
    // names the files picked before, and picking those same files again still fires onChange.
    if (inputRef.current !== null) {
      inputRef.current.value = "";
    }
    chooseFiles(files);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSending(true);
    // One file at a time: the API reads each upload into memory, and the results then show
    // in the order of the list.
    for (const row of rows) {
      if (row.result !== null) {
        continue;
      }
      const reason = await checkPdf(row.file);
      if (reason !== null) {
        setResult(row.id, { state: "rejected", reason });
        continue;
      }
      setResult(row.id, { state: "sending" });
      setResult(row.id, await sendFile(row.file, parser));
    }
    setSending(false);
  }

  const unsent = rows.some((row) => row.result === null);
  const done = rows.length > 0 && !sending && !unsent;
  const started = rows.filter((row) => row.result?.state === "started").length;

  return (
    <>
      <h1>Start a job</h1>
      <p>Each PDF becomes its own job.</p>
      <form onSubmit={handleSubmit} className="job-form">
        <fieldset disabled={sending} className="job-fieldset">
          {/* biome-ignore lint/a11y/noStaticElementInteractions: dropping is a shortcut; the file input inside does the same with a keyboard. */}
          <div
            className={dragging ? "job-drop job-drop-active" : "job-drop"}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <label className="job-field">
              PDF files
              <input
                ref={inputRef}
                type="file"
                name="files"
                multiple
                accept="application/pdf,.pdf"
                onChange={(event) => chooseFiles(event.currentTarget.files)}
              />
            </label>
            <p className="job-drop-hint">or drop them here. Up to 20 MiB each.</p>
          </div>
          <fieldset className="job-parsers">
            <legend>Parser</legend>
            {PARSERS.map(({ name, label }) => (
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
            <dd>{PARSERS.find(({ name }) => name === parser)?.label}</dd>
            <dt>Extractor</dt>
            <dd>{EXTRACTOR.label}</dd>
            <dt>Traits</dt>
            <dd>{TRAITS.join(", ")}</dd>
          </dl>
        </section>
        <button type="submit" disabled={sending || !unsent}>
          {sending ? "Starting jobs…" : "Start jobs"}
        </button>
      </form>
      {rows.length > 0 && (
        <ul aria-label="Files" className="job-files">
          {rows.map((row) => (
            <li key={row.id} className="job-file">
              <span className="job-file-name">{row.file.name}</span>
              <FileStatus result={row.result} />
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

// Uploads the file, then starts its job. Never throws: a failure becomes the reason to show.
async function sendFile(file: File, parser: ParserName): Promise<FileResult> {
  try {
    const document = await uploadDocument(file);
    const job = await startDocumentJob(document.checksum, { parser, extractor: EXTRACTOR.name });
    return { state: "started", jobId: job.job_id };
  } catch (error) {
    return { state: "failed", reason: failureReason(error) };
  }
}

function failureReason(error: unknown): string {
  if (error instanceof ApiError) {
    // A 413 can come from a proxy in front of the API, with a plain-text body and no message.
    return error.status === 413 ? TOO_LARGE : error.message;
  }
  // fetch rejects with a TypeError when the request never got an answer.
  return "The API could not be reached. Try again.";
}

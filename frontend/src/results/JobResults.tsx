import { type MouseEvent, useState } from "react";
import { ApiError, type EtsRow, type EtsRowsResponse } from "../api/client";
import { shouldRetry } from "../api/queryClient";
import { toCsv } from "./csv";
import { downloadName } from "./fileName";
import { reportedText, taxonText } from "./format";
import { saveFile } from "./saveFile";
import { useJobResults } from "./useJobResults";
import { flagsRow, informationalLabel, parseWarnings, warningText } from "./warnings";

interface JobResultsProps {
  jobId: string;
  displayName: string | null;
  // The observation whose evidence is open, from 0, or null.
  selectedIndex: number | null;
  onSelect: (observationIndex: number) => void;
}

// A done job's observations: one table row per ETS row, the warnings of each, and the
// downloads. Both files are built here from the loaded response.
export function JobResults({ jobId, displayName, selectedIndex, onSelect }: JobResultsProps) {
  const results = useJobResults(jobId);
  const [hideFlagged, setHideFlagged] = useState(false);

  if (results.isPending) {
    return (
      <p role="status">
        {results.failureCount > 0
          ? "The results are temporarily unavailable. Retrying…"
          : "Loading results…"}
      </p>
    );
  }
  if (results.isError) {
    const { message, canRetry } = resultsError(results.error);
    return (
      <div role="alert" className="results-error">
        <p>{message}</p>
        {canRetry && (
          <button type="button" onClick={() => void results.refetch()}>
            Try again
          </button>
        )}
      </div>
    );
  }

  const body = results.data;
  // The position in the response is the observation's index in the extraction artifact:
  // the API returns the rows in observation order. It stays the same when rows are hidden.
  const entries = body.rows.map((row, index) => {
    const codes = parseWarnings(row.warnings);
    return { row, index, codes, flagged: codes.some(flagsRow) };
  });
  const flaggedCount = entries.filter((entry) => entry.flagged).length;
  const shown = hideFlagged ? entries.filter((entry) => !entry.flagged) : entries;

  return (
    <div className="results">
      <Downloads jobId={jobId} displayName={displayName} body={body} />
      {entries.length === 0 ? (
        <p>No observations were found in this document.</p>
      ) : (
        <>
          <div className="results-toolbar">
            <label>
              <input
                type="checkbox"
                checked={hideFlagged}
                onChange={(event) => setHideFlagged(event.target.checked)}
              />{" "}
              Hide flagged rows
            </label>
            <p role="status" className="results-count">
              {hideFlagged
                ? `${rowCount(flaggedCount)} hidden. Showing ${shown.length} of ${entries.length}.`
                : `${rowCount(flaggedCount)} of ${entries.length} flagged.`}
            </p>
          </div>
          {shown.length === 0 ? (
            <p>Every observation is flagged. Clear “Hide flagged rows” to see them.</p>
          ) : (
            <ResultsTable entries={shown} selectedIndex={selectedIndex} onSelect={onSelect} />
          )}
        </>
      )}
    </div>
  );
}

function Downloads({
  jobId,
  displayName,
  body,
}: {
  jobId: string;
  displayName: string | null;
  body: EtsRowsResponse;
}) {
  // The files hold every row: hiding flagged rows changes only the table.
  function downloadCsv() {
    const csv = new Blob([toCsv(body.rows)], { type: "text/csv;charset=utf-8" });
    saveFile(downloadName(displayName, jobId, "csv"), csv);
  }
  function downloadJson() {
    const json = new Blob([`${JSON.stringify(body, null, 2)}\n`], { type: "application/json" });
    saveFile(downloadName(displayName, jobId, "json"), json);
  }
  return (
    <div className="results-downloads">
      <button type="button" onClick={downloadCsv}>
        Download CSV
      </button>
      <button type="button" onClick={downloadJson}>
        Download JSON
      </button>
    </div>
  );
}

interface Entry {
  row: EtsRow;
  index: number;
  codes: string[];
  // At least one code flags the row (see INFORMATIONAL in warnings.ts).
  flagged: boolean;
}

function ResultsTable({
  entries,
  selectedIndex,
  onSelect,
}: {
  entries: Entry[];
  selectedIndex: number | null;
  onSelect: (observationIndex: number) => void;
}) {
  // A click anywhere on a row opens its evidence, except on a control inside it, such as
  // the warnings toggle. The button in the first cell is the keyboard path.
  function handleRowClick(event: MouseEvent<HTMLTableRowElement>, index: number) {
    if (event.target instanceof Element && event.target.closest("button, a, input, details")) {
      return;
    }
    onSelect(index);
  }
  return (
    // biome-ignore lint/a11y/noNoninteractiveTabindex: scroll regions need keyboard access
    <section className="jobs-scroll results-scroll" aria-label="Observations" tabIndex={0}>
      <table className="jobs-table results-table">
        <caption>Observations, in extraction order</caption>
        <thead>
          <tr>
            {["No.", "Taxon", "Trait", "Reported value", "Flag"].map((heading) => (
              <th key={heading} scope="col">
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {entries.map(({ row, index, codes, flagged }) => {
            const selected = index === selectedIndex;
            const reported = reportedText(row);
            return (
              <tr
                key={row.measurementID}
                className={selected ? "results-selected" : undefined}
                aria-current={selected ? "true" : undefined}
                onClick={(event) => handleRowClick(event, index)}
              >
                <td>
                  <button
                    type="button"
                    className="results-open"
                    aria-label={`Show evidence for observation ${index + 1}`}
                    onClick={() => onSelect(index)}
                  >
                    {index + 1}
                  </button>
                </td>
                <td className="results-taxon">{taxonText(row)}</td>
                <td className="results-trait">{row.verbatimTraitName}</td>
                <td>{reported ?? <span className="results-empty">Not reported</span>}</td>
                <td>
                  <Flag codes={codes} flagged={flagged} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

// A disclosure, so the warnings open by click, Enter, or Space, and are not hover-only.
// A row with only informational codes is not flagged: its summary names them without the
// flag mark, and the list still explains each code.
function Flag({ codes, flagged }: { codes: string[]; flagged: boolean }) {
  if (codes.length === 0) {
    return <span className="results-empty">None</span>;
  }
  return (
    <details className={flagged ? "results-flag" : "results-flag results-note"}>
      <summary>
        {flagged ? (
          <>
            <span aria-hidden="true">⚑ </span>
            {codes.length === 1 ? "1 warning" : `${codes.length} warnings`}
          </>
        ) : (
          [...new Set(codes.map((code) => informationalLabel(code)))].join(", ")
        )}
      </summary>
      <ul>
        {codes.map((code, position) => {
          const text = warningText(code);
          return (
            // A code can repeat in principle, so its position is part of the key.
            // biome-ignore lint/suspicious/noArrayIndexKey: the list never reorders.
            <li key={`${position}-${code}`}>
              <code>{code}</code>
              {text !== null && <> — {text}</>}
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function rowCount(count: number): string {
  return count === 1 ? "1 row" : `${count} rows`;
}

function resultsError(error: Error): { message: string; canRetry: boolean } {
  if (error instanceof ApiError) {
    if (error.status === 500) {
      // artifact_invalid, or job_state_unreadable: read again, they fail the same way.
      return {
        message: "The results of this job cannot be read. No downloads are available.",
        canRetry: false,
      };
    }
    if (error.status === 503) {
      return {
        message: "The results are temporarily unavailable. Try again in a moment.",
        canRetry: true,
      };
    }
    if (error.status === 409) {
      return { message: "This job has no results yet.", canRetry: true };
    }
    if (!shouldRetry(0, error)) {
      return {
        message: "The results could not be loaded. Check your access and try again.",
        canRetry: false,
      };
    }
  }
  return {
    message: "The results could not be loaded. Check the connection and try again.",
    canRetry: true,
  };
}

import { type KeyboardEvent, type ReactNode, useEffect, useRef } from "react";
import { ApiError, type ParseProblem } from "../api/client";
import { EvidencePdf } from "./EvidencePdf";
import {
  buildQuotation,
  EvidenceMismatchError,
  type Observation,
  type QuotedBlock,
  type TextBlock,
} from "./quotation";
import { useEvidenceArtifacts } from "./useEvidenceArtifacts";

interface EvidenceViewProps {
  jobId: string;
  // The index of the observation in the extraction artifact, from 0.
  observationIndex: number;
  onClose: () => void;
}

// Where a value came from: the quotation from the parser block, with the value and the unit
// marked. Shown only once an observation is selected, so nothing loads before that.
export function EvidenceView({ jobId, observationIndex, onClose }: EvidenceViewProps) {
  const { parser, observations } = useEvidenceArtifacts(jobId, true);
  const notices = problemNotices(parser.data?.problems ?? []);
  const panelRef = useRef<HTMLElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  // The element that opened this observation, such as a results row, gets focus back on close.
  // Selecting another row while the view is open makes that row the one to return to.
  // biome-ignore lint/correctness/useExhaustiveDependencies: runs for each observation shown.
  useEffect(() => {
    const active = document.activeElement;
    if (active instanceof HTMLElement && !panelRef.current?.contains(active)) {
      openerRef.current = active;
    }
  }, [observationIndex]);

  // Keyboard and screen reader users are taken to the view when it opens.
  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  // A block can be a long paragraph, so the value is scrolled into view once its quotation
  // shows, and again for each observation. This runs after the focus above, which would
  // otherwise scroll the page back to the heading.
  // biome-ignore lint/correctness/useExhaustiveDependencies: runs when the quotation changes.
  useEffect(() => {
    panelRef.current
      ?.querySelector(".evidence-quote mark.evidence-value")
      ?.scrollIntoView({ block: "center" });
  }, [observationIndex, observations.data, parser.data]);

  function close() {
    const opener = openerRef.current;
    onClose();
    if (opener?.isConnected) {
      opener.focus();
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      close();
    }
  }

  const observation = observations.data?.[observationIndex];
  return (
    <section
      ref={panelRef}
      aria-labelledby="evidence-heading"
      className="evidence-panel"
      onKeyDown={handleKeyDown}
    >
      <div className="evidence-header">
        <h2 id="evidence-heading" ref={headingRef} tabIndex={-1}>
          Evidence for observation {observationIndex + 1}
        </h2>
        <button type="button" onClick={close}>
          Close
        </button>
      </div>
      {notices.map((notice) => (
        <p key={notice.code} role="status">
          {notice.text}
        </p>
      ))}
      {observations.isPending ? (
        <p role="status">Loading the evidence…</p>
      ) : observations.isError ? (
        <EvidenceError>{artifactError(observations.error, "extraction")}</EvidenceError>
      ) : observation === undefined ? (
        <EvidenceError>
          This job has no observation {observationIndex + 1}. It has {observations.data.length}.
        </EvidenceError>
      ) : (
        <>
          <ValueAndUnit observation={observation} />
          {parser.isPending ? (
            <p role="status">Loading the quotation…</p>
          ) : parser.isError ? (
            <EvidenceError>{artifactError(parser.error, "parser")}</EvidenceError>
          ) : (
            <Quotation jobId={jobId} observation={observation} blocks={parser.data.blocks} />
          )}
        </>
      )}
    </section>
  );
}

// Advent problems share one notice. The lead names the typesetter, then one sentence each.
// A failed check does not know the typesetter, so that notice has no lead.
// An unknown code has no sentence yet.
function problemNotices(problems: ParseProblem[]): { code: string; text: string }[] {
  const details: string[] = [];
  for (const problem of problems) {
    const text = problemText(problem);
    if (text !== null) {
      details.push(text);
    }
  }
  if (details.length === 0) {
    return [];
  }
  const failed = problems.every((problem) => problem.code === "advent_fonts_unchecked");
  const text = failed
    ? details.join(" ")
    : `This paper uses the 3B2 typesetter. ${details.join(" ")}`;
  return [{ code: "advent", text }];
}

function problemText(problem: ParseProblem): string | null {
  if (problem.code === "advent_fonts_unchecked") {
    return "The symbol check did not finish, so this paper could not be confirmed free of the rare 3B2 typesetter. If it uses 3B2, a symbol in the parsed text may not be the symbol printed on the page.";
  }
  if (problem.code === "advent_symbols_replaced" && problem.count >= 1) {
    if (problem.count === 1) {
      return "1 Advent symbol in the parsed text was replaced with the Unicode replacement character because the printed symbol could not be named.";
    }
    return `${problem.count} Advent symbols in the parsed text were replaced with the Unicode replacement character because the printed symbols could not be named.`;
  }
  if (problem.code === "advent_characters_unverified" && problem.count >= 1) {
    if (problem.count === 1) {
      return "1 Advent character in the parsed text was left unchanged because the font has not been verified.";
    }
    return `${problem.count} Advent characters in the parsed text were left unchanged because the font has not been verified.`;
  }
  if (problem.code === "advent_pages_uncounted" && problem.count >= 1) {
    if (problem.count === 1) {
      return "On 1 page, the drawing commands could not be counted, so an Advent symbol in the parsed text may have been replaced with the Unicode replacement character without that replacement being reported.";
    }
    return `On ${problem.count} pages, the drawing commands could not be counted, so an Advent symbol in the parsed text may have been replaced with the Unicode replacement character without that replacement being reported.`;
  }
  return null;
}

// The printed value and unit, from the extraction artifact alone, so they stay when the
// parser artifact cannot be loaded. The labels say which mark is which without color.
function Bound({ label, value }: { label: string; value: Observation["measurementValueMin"] }) {
  if (value == null) {
    return null;
  }
  return (
    <>
      <dt>{label}</dt>
      <dd>
        <mark className="evidence-value">{printedText(value)}</mark>
      </dd>
    </>
  );
}

function printedText(value: NonNullable<Observation["measurementValueMin"]>): string {
  return value.kind === "citation" ? value.text : value.value;
}

function ValueAndUnit({ observation }: { observation: Observation }) {
  return (
    <dl className="evidence-legend">
      {observation.verbatimTraitValue != null ? (
        <>
          <dt>Value</dt>
          <dd>
            <mark className="evidence-value">{observation.verbatimTraitValue.text}</mark>
          </dd>
        </>
      ) : (
        <>
          <Bound label="Minimum" value={observation.measurementValueMin} />
          <Bound label="Maximum" value={observation.measurementValueMax} />
        </>
      )}
      {observation.verbatimTraitUnit != null && (
        <>
          <dt>Unit</dt>
          <dd>
            <mark className="evidence-unit">{observation.verbatimTraitUnit.text}</mark>
          </dd>
        </>
      )}
    </dl>
  );
}

function Quotation({
  jobId,
  observation,
  blocks,
}: {
  jobId: string;
  observation: Observation;
  blocks: ReadonlyMap<string, TextBlock>;
}) {
  let quotation: QuotedBlock[];
  try {
    quotation = buildQuotation(observation, blocks);
  } catch (error) {
    if (error instanceof EvidenceMismatchError) {
      return (
        <EvidenceError>
          The extraction results do not match the parsed text. {error.message}
        </EvidenceError>
      );
    }
    throw error;
  }
  return (
    <>
      {quotation.map((block) => (
        <figure key={block.blockId} className="evidence-block">
          <figcaption>
            Block <code>{block.blockId}</code>, page {block.pageNumber}
          </figcaption>
          <blockquote className="evidence-quote">
            <Segments block={block} />
          </blockquote>
        </figure>
      ))}
      <h3 className="evidence-subheading">In the PDF</h3>
      <EvidencePdf jobId={jobId} quotation={quotation} />
    </>
  );
}

function Segments({ block }: { block: QuotedBlock }) {
  // A segment's offset in the block is its key: it is unique and stays the same.
  let offset = 0;
  return block.segments.map((segment) => {
    const key = offset;
    offset += segment.text.length;
    return segment.mark === null ? (
      <span key={key}>{segment.text}</span>
    ) : (
      <mark key={key} className={`evidence-${segment.mark}`}>
        {segment.text}
      </mark>
    );
  });
}

function EvidenceError({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="evidence-error">
      {children}
    </p>
  );
}

export function artifactError(error: Error, kind: "parser" | "extraction"): string {
  const name = kind === "parser" ? "parsed text" : "extraction results";
  if (!(error instanceof ApiError)) {
    return `The ${name} could not be loaded. Check the connection and reopen the observation.`;
  }
  switch (error.status) {
    case 404:
      return "This job does not exist.";
    case 409:
      return kind === "parser"
        ? "This job has no parsed text yet. It is stored once the PDF is parsed."
        : "This job has no extraction results yet. They are stored once the job is done.";
    case 500:
      return `The stored ${name} of this job cannot be read.`;
    default:
      return error.message;
  }
}

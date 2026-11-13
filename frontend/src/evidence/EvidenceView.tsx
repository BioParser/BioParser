import { type KeyboardEvent, type ReactNode, useEffect, useRef } from "react";
import { ApiError } from "../api/client";
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
  const { blocks, observations } = useEvidenceArtifacts(jobId, true);
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
  }, [observationIndex, observations.data, blocks.data]);

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
          {blocks.isPending ? (
            <p role="status">Loading the quotation…</p>
          ) : blocks.isError ? (
            <EvidenceError>{artifactError(blocks.error, "parser")}</EvidenceError>
          ) : (
            <Quotation jobId={jobId} observation={observation} blocks={blocks.data} />
          )}
        </>
      )}
    </section>
  );
}

// The printed value and unit, from the extraction artifact alone, so they stay when the
// parser artifact cannot be loaded. The labels say which mark is which without color.
function ValueAndUnit({ observation }: { observation: Observation }) {
  return (
    <dl className="evidence-legend">
      <dt>Value</dt>
      <dd>
        <mark className="evidence-value">{observation.verbatimTraitValue.text}</mark>
      </dd>
      <dt>Unit</dt>
      <dd>
        <mark className="evidence-unit">{observation.verbatimTraitUnit.text}</mark>
      </dd>
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

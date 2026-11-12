import { useParams, useSearchParams } from "react-router";
import { EvidenceView } from "../evidence/EvidenceView";

// The observation whose evidence is open, as `?observation=N` (an index from 0). Until the
// results table exists, this is how the evidence view is opened.
const OBSERVATION_PARAM = "observation";

export function JobDetailPage() {
  const { jobId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const observationIndex = parseObservationIndex(searchParams.get(OBSERVATION_PARAM));

  //
  function closeEvidence() {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      next.delete(OBSERVATION_PARAM);
      return next;
    });
  }

  const showEvidence = jobId !== undefined && observationIndex !== null;
  return (
    <div className={showEvidence ? "job-detail job-detail-with-evidence" : "job-detail"}>
      <div>
        <h1>Job</h1>
        <p>
          Job ID: <code>{jobId}</code>
        </p>
      </div>
      {showEvidence && (
        <EvidenceView jobId={jobId} observationIndex={observationIndex} onClose={closeEvidence} />
      )}
    </div>
  );
}

function parseObservationIndex(value: string | null): number | null {
  return value !== null && /^\d+$/.test(value) ? Number(value) : null;
}
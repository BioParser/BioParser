import { useParams } from "react-router";

export function JobDetailPage() {
  const { jobId } = useParams();
  return (
    <>
      <h1>Job</h1>
      <p>
        Job ID: <code>{jobId}</code>
      </p>
    </>
  );
}

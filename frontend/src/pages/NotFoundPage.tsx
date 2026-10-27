import { Link } from "react-router";

export function NotFoundPage() {
  return (
    <>
      <h1>Page not found</h1>
      <p>
        <Link to="/">Go to the dashboard</Link>
      </p>
    </>
  );
}

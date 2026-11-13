import { Link, NavLink, Outlet } from "react-router";
import { useHasApiKey } from "../api/useHasApiKey";
import { ApiKeyPage } from "../pages/ApiKeyPage";

export function Layout() {
  const hasApiKey = useHasApiKey();
  return (
    <>
      <header className="layout-header">
        <Link to="/" className="layout-brand">
          BioParser
        </Link>
        <nav aria-label="Main" className="layout-nav">
          {/* end: "/" is active only on the dashboard, not on every path under it. */}
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/jobs/new">Start a job</NavLink>
          <NavLink to="/key">API key</NavLink>
        </nav>
      </header>
      <main className="layout-main">
        {/* Without a key, the key screen takes the page's place. The URL stays the same,
            so the requested page shows as soon as a key is entered. */}
        {hasApiKey ? <Outlet /> : <ApiKeyPage />}
      </main>
    </>
  );
}

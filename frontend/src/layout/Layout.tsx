import { Link, NavLink, Outlet } from "react-router";

export function Layout() {
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
        <Outlet />
      </main>
    </>
  );
}

import type { RouteObject } from "react-router";
import { Layout } from "./layout/Layout";
import { ApiKeyPage } from "./pages/ApiKeyPage";
import { DashboardPage } from "./pages/DashboardPage";
import { JobDetailPage } from "./pages/JobDetailPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { StartJobPage } from "./pages/StartJobPage";

// Paths are relative to /ui, the basename in main.tsx.
export const routes: RouteObject[] = [
  {
    element: <Layout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "key", element: <ApiKeyPage /> },
      { path: "jobs/new", element: <StartJobPage /> },
      { path: "jobs/:jobId", element: <JobDetailPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

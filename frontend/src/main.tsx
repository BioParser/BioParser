import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import "./index.css";
import { routes } from "./routes";

// BASE_URL is Vite's `base` ("/ui/"), so the router and the build agree on the prefix.
const router = createBrowserRouter(routes, {
  basename: import.meta.env.BASE_URL.replace(/\/$/, ""),
});
const queryClient = new QueryClient();

const root = document.getElementById("root");
if (root === null) {
  throw new Error('index.html has no element with id "root"');
}

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);

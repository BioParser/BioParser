import { defineConfig, devices } from "@playwright/test";

// Browser tests for what jsdom cannot see: layout. They run against the built UI, so run
// `npm run build` first. Every API call is answered by a mock in the test itself.
const PORT = 4174;

export default defineConfig({
  testDir: "e2e",
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npx vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}/ui/`,
    reuseExistingServer: !process.env.CI,
  },
});

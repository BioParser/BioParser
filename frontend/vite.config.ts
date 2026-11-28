import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Host `npm run dev` proxies to the API on this machine. Compose sets
// BIOPARSER_DEV_API_ORIGIN=http://bioparser:8080 because 127.0.0.1 inside the
// frontend container is that container.
const devApiOrigin = process.env.BIOPARSER_DEV_API_ORIGIN || "http://127.0.0.1:8080";

export default defineConfig({
  base: "/ui/",
  plugins: [react()],
  server: {
    proxy: { "/api": devApiOrigin, "/ready": devApiOrigin },
  },
  test: {
    environment: "jsdom",
    // Unit tests only. The browser tests in e2e/ run under Playwright (npm run test:e2e).
    include: ["src/**/*.test.{ts,tsx}"],
    // Vitest stubs CSS files. contrast.test.ts reads the design tokens, so this one is real.
    css: { include: [/tokens\.css/] },
  },
});

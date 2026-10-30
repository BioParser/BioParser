import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  base: "/ui/",
  plugins: [react()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8080", "/ready": "http://127.0.0.1:8080" },
  },
  test: { environment: "jsdom" },
});

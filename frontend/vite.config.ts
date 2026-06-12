/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // Relative asset paths so a production build loads correctly from a file:// origin when the
  // Electron shell does loadFile(frontend/dist/index.html). Harmless for the dev server and the
  // plain-browser deploy. (Without this, file:// requests /assets/* from the FS root → blank page.)
  base: "./",
  plugins: [react()],
  server: {
    // Proxy API calls to the FastAPI backend during development. Every backend route
    // prefix the SPA calls must be listed here, or the request hits Vite (404) instead.
    proxy: {
      "/portfolios": "http://localhost:8000",
      "/settings": "http://localhost:8000",
      "/securities": "http://localhost:8000",
      "/macro": "http://localhost:8000",
      "/factors": "http://localhost:8000",
      "/people": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});

/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // Fixed, non-default port: Vite's own default (5173) is already claimed by CATO's dev
    // server, and the Iridium app family assigns each app its own port to run concurrently
    // (CATO 5173, Mercury 5174, Vulcan/Iridium-Backend 5175) — this was the one left on the
    // shared default, silently colliding with CATO's.
    port: 5176,
    // Proxy API calls to the FastAPI backend during development. Every backend route
    // prefix the SPA calls must be listed here, or the request hits Vite (404) instead.
    proxy: {
      "/portfolios": "http://localhost:8000",
      "/settings": "http://localhost:8000",
      "/securities": "http://localhost:8000",
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

/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Neptune's own dev-server/API allocation: distinct from Mercury/Vulcan/CATO (all Vite
// 5173) and Mercury/Vulcan (both API 8432), so each Iridium-suite app has a stable origin
// for the upcoming identity service (OIDC loopback callbacks, JWT `aud`, and CORS
// allowlists are all scheme+host+port-specific). API_TARGET must match neptune.config's
// api_host/api_port default (127.0.0.1:8000) unless NEPTUNE_API_PORT is overridden.
const API_TARGET = "http://localhost:8000";

export default defineConfig({
  // Relative asset paths so a production build loads correctly from a file:// origin when the
  // Electron shell does loadFile(frontend/dist/index.html). Harmless for the dev server and the
  // plain-browser deploy. (Without this, file:// requests /assets/* from the FS root → blank page.)
  base: "./",
  plugins: [react()],
  server: {
    port: 5176,
    strictPort: true, // fail loudly on a clash rather than silently landing on a port
                       // nothing (the Electron shell, other Iridium apps) expects
    // Proxy API calls to the FastAPI backend during development. Every backend route
    // prefix the SPA calls must be listed here, or the request hits Vite (404) instead.
    proxy: {
      "/portfolios": API_TARGET,
      "/settings": API_TARGET,
      "/securities": API_TARGET,
      "/macro": API_TARGET,
      "/factors": API_TARGET,
      "/people": API_TARGET,
      "/health": API_TARGET,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});

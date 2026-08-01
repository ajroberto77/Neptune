/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Deep Ocean palette (roadmap Phase 4). Calibrated 2026-08 for WCAG AA against
        // both ocean-bg and ocean-panel — see docs/ (readability audit). Same hue
        // families throughout; only lightness/saturation adjusted for contrast.
        ocean: {
          bg: "#0a0e1a",
          panel: "#111726",
          // was #1f2a44 (1.25-1.35:1 vs bg/panel, well under the 3:1 non-text minimum —
          // the app has no shadow/elevation system, so this line is the ONLY thing
          // separating a card from the page or one table row from the next).
          border: "#33456b",
          accent: "#3b82f6",
          secondary: "#818cf8",
          // was #64748b (3.76-4.05:1 vs panel/bg — fails AA at the app's single most-used
          // text token, ~150+ call sites: table headers, captions, secondary data).
          muted: "#94a3b8",
          // Tertiary/decorative tier (item counts, empty-state dashes) — intentionally
          // below body-text AA since it's never load-bearing content. Replaces the four
          // ad-hoc ocean-muted/50-80 opacity values previously used for this inconsistently.
          faint: "#586686",
        },
        status: {
          ok: "#22c55e",
          watch: "#f59e0b",
          // was #ef4444 (4.12:1 as badge text on its own bg-status-breach/15 fill — the
          // WORST-contrast status color, on the one signal that must never be missed).
          breach: "#f2604f",
        },
      },
      fontFamily: {
        display: ["Outfit", "system-ui", "sans-serif"],
        body: ["Inter", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};

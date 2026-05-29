/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Deep Ocean palette (roadmap Phase 4)
        ocean: {
          bg: "#0a0e1a",
          panel: "#111726",
          border: "#1f2a44",
          accent: "#3b82f6",
          secondary: "#818cf8",
          muted: "#64748b",
        },
        status: {
          ok: "#22c55e",
          watch: "#f59e0b",
          breach: "#ef4444",
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

// TitleBar — the app's top brand bar, modeled on the Iridium desktop suite's TitleBar
// (glyph mark + mono wordmark + subtitle + backend status pill + settings gear), restyled into
// Neptune's dark "Deep Ocean" palette. Purely presentational.

interface Props {
  /** Backend health / activity, surfaced as the status pill. */
  status?: "ready" | "running" | "error";
  /** Optional override for the pill label. */
  statusText?: string;
  subtitle?: string;
  /** When true, the backend is running on throwaway SQLite + synthetic data — flag it loudly. */
  testMode?: boolean;
  onSettings?: () => void;
}

// The Neptune brand mark — trident glyph only, no background chip, from the official kit
// (assets/brand/Neptune - Glyph White.svg), inlined so it sizes/tints via CSS like the rest of
// the title bar.
function Glyph() {
  return (
    <svg
      className="h-[22px] w-[22px] flex-shrink-0"
      viewBox="0 0 148 148"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M74.133 29C74.5592 29.3547 79.6687 43.9229 80.4537 45.8752C79.5503 45.8458 78.5656 45.8587 77.6553 45.853C77.6348 46.4369 77.6263 47.0323 77.6346 47.6204C77.7738 57.5735 77.6254 67.5587 77.9989 77.4988C78.0253 77.9234 78.906 78.8982 79.2644 79.0175C81.0711 79.5436 83.6891 79.193 85.5026 78.7402C92.1357 77.0838 92.0349 73.3547 92.6416 67.9265C93.2235 62.7224 94.2187 53.3714 96.8888 49.0853C96.0724 48.9615 95.0716 48.9159 94.2322 48.8575C96.9849 45.2738 100.833 42.4707 105.065 40.8354C106.401 40.3187 108.336 39.9217 109.77 39.5943C108.379 40.892 107.011 42.2009 105.865 43.7592C100.059 51.6549 100.397 61.2059 99.3809 70.4813C99.1716 72.7961 99.0732 75.0814 98.6606 77.3674C97.5625 83.4527 92.4627 86.3425 86.8436 87.503C83.3592 88.2226 79.1241 88.3478 78.3041 92.6512C77.8614 94.9747 79.4425 95.065 80.5346 96.7077C82.1891 99.1635 80.1949 100.807 78.2275 102.043C78.0155 103.586 78.0396 107.02 77.992 108.701L77.6096 119L73.918 118.98L71.0545 118.939L70.3105 101.996C69.3966 101.459 68.753 101.194 68.085 100.305C67.5674 99.6236 67.3512 98.7604 67.4866 97.9158C67.7338 96.2968 69.005 95.4158 70.2096 94.5221C70.597 87.6443 64.3712 88.4467 59.4745 87.048C48.9808 84.0505 49.5213 76.8992 48.8463 67.8025C48.6066 64.9567 48.3174 62.1152 47.9789 59.2794C47.0846 51.4415 44.5687 44.5868 38.2303 39.55C39.6509 39.9146 41.1627 40.1562 42.5522 40.6415C47.6765 42.4315 50.849 44.9587 54.3519 48.8858C53.3575 48.9127 52.6294 48.9188 51.6496 49.0837C54.9736 55.7034 55.2961 63.98 56.287 71.2523C56.599 73.5416 56.9729 75.561 59.0151 77.0952C61.3269 78.8318 65.1159 79.3497 67.9508 79.1944C69.0507 79.1342 69.9189 78.3589 70.5503 77.5218C70.7481 74.1352 70.6543 69.9661 70.6973 66.5328C70.8308 59.6625 70.9005 52.7912 70.9065 45.9197C69.9313 45.9187 68.8485 45.8641 67.8649 45.8361C68.7609 44.109 69.7331 41.1365 70.407 39.2466C71.6238 35.8219 72.8657 32.4063 74.133 29Z"
        fill="white"
      />
    </svg>
  );
}

export function TitleBar({
  status = "ready",
  statusText,
  subtitle = "Portfolio and Risk Management Platform",
  testMode = false,
  onSettings,
}: Props) {
  // Test Mode subsumes the ordinary status pill rather than adding a second one next to it —
  // "running on synthetic data" is a more important thing to know than "the backend answered a
  // health check", so it wins the one pill slot instead of competing for attention with it.
  const pill = testMode
    ? "Test Mode · Synthetic Data"
    : (statusText ?? (status === "running" ? "Working" : status === "error" ? "Offline" : "Ready"));
  const pillCls = testMode
    ? "border-status-watch/50 bg-status-watch/10 text-status-watch"
    : status === "error"
      ? "border-status-breach/50 text-status-breach"
      : status === "running"
        ? "border-status-ok/50 text-status-ok"
        : "border-ocean-border text-ocean-muted";

  // Window controls only exist when running inside the Electron shell (frameless window).
  const bridge = typeof window !== "undefined" ? window.neptune : undefined;
  const hasWindowControls = Boolean(bridge?.closeWindow);
  const ctrlBtn =
    "app-no-drag flex h-8 w-11 items-center justify-center text-ocean-muted transition hover:bg-white/10 hover:text-white";

  return (
    <div className="app-drag flex h-12 flex-shrink-0 items-center gap-3 border-b border-ocean-border bg-ocean-panel pl-4">
      <Glyph />
      <span className="font-mono text-lg font-bold uppercase tracking-[0.2em] text-white">
        Neptune
      </span>
      <span className="h-3.5 w-px bg-white/15" />
      <span className="font-display text-sm font-light text-ocean-muted">{subtitle}</span>
      <div className="flex-1" />
      <span
        className={`app-no-drag rounded-full border px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-wide ${pillCls}`}
        aria-label="backend-status"
      >
        {pill}
      </span>
      <button
        onClick={onSettings}
        title="Settings"
        aria-label="open-settings"
        className="app-no-drag mr-1 flex h-8 w-8 items-center justify-center rounded text-lg text-ocean-muted transition hover:bg-white/10 hover:text-white"
      >
        ⚙
      </button>

      {hasWindowControls && (
        <div className="flex items-center">
          <button
            onClick={() => bridge?.minimizeWindow?.()}
            title="Minimize"
            aria-label="window-minimize"
            className={ctrlBtn}
          >
            <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
              <line x1="1" y1="5" x2="9" y2="5" stroke="currentColor" strokeWidth="1.2" />
            </svg>
          </button>
          <button
            onClick={() => bridge?.toggleMaximizeWindow?.()}
            title="Maximize"
            aria-label="window-maximize"
            className={ctrlBtn}
          >
            <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
              <rect x="1.2" y="1.2" width="7.6" height="7.6" fill="none" stroke="currentColor" strokeWidth="1.2" />
            </svg>
          </button>
          <button
            onClick={() => bridge?.closeWindow?.()}
            title="Close"
            aria-label="window-close"
            className="app-no-drag flex h-8 w-11 items-center justify-center text-ocean-muted transition hover:bg-status-breach hover:text-white"
          >
            <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
              <line x1="1.5" y1="1.5" x2="8.5" y2="8.5" stroke="currentColor" strokeWidth="1.2" />
              <line x1="8.5" y1="1.5" x2="1.5" y2="8.5" stroke="currentColor" strokeWidth="1.2" />
            </svg>
          </button>
        </div>
      )}
    </div>
  );
}

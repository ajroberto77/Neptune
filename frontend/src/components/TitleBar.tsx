// TitleBar — the app's top brand bar, modeled on the Iridium desktop suite's TitleBar
// (gem mark + mono wordmark + subtitle + backend status pill + settings gear), restyled into
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

// A small faceted "gem" mark, echoing the suite's logo so Neptune reads as a sibling app —
// tinted to Neptune's blues/teal rather than Iridium's gold.
function Gem() {
  return (
    <svg
      className="h-[18px] w-[18px] flex-shrink-0"
      viewBox="0 0 17 17"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <polygon
        points="8.5,1.5 14.5,5 14.5,12 8.5,15.5 2.5,12 2.5,5"
        fill="none"
        stroke="rgba(129,140,248,0.55)"
        strokeWidth="0.9"
      />
      <polygon points="8.5,1.5 14.5,5 8.5,8.5" fill="rgba(59,130,246,0.45)" />
      <polygon points="8.5,8.5 14.5,5 14.5,12" fill="rgba(59,130,246,0.28)" />
      <polygon points="8.5,8.5 14.5,12 8.5,15.5" fill="rgba(129,140,248,0.22)" />
      <polygon points="8.5,8.5 8.5,15.5 2.5,12" fill="rgba(34,197,94,0.26)" />
      <polygon points="8.5,8.5 2.5,12 2.5,5" fill="rgba(59,130,246,0.34)" />
      <polygon points="8.5,8.5 2.5,5 8.5,1.5" fill="rgba(129,140,248,0.30)" />
      <circle cx="8.5" cy="8.5" r="1.4" fill="rgba(232,239,247,0.7)" />
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
  const pill =
    statusText ?? (status === "running" ? "Working" : status === "error" ? "Offline" : "Ready");
  const pillCls =
    status === "error"
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
      <Gem />
      <span className="font-mono text-lg font-bold uppercase tracking-[0.2em] text-white">
        Neptune
      </span>
      <span className="h-3.5 w-px bg-white/15" />
      <span className="font-display text-sm font-light text-ocean-muted">{subtitle}</span>
      <div className="flex-1" />
      {testMode && (
        <span
          className="app-no-drag rounded-full border border-status-watch/50 bg-status-watch/10 px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-status-watch"
          aria-label="test-mode"
        >
          Test Mode · synthetic data
        </span>
      )}
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

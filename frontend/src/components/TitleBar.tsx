// TitleBar — the app's top brand bar, modeled on the Iridium desktop suite's TitleBar
// (badge mark + mono wordmark + subtitle + backend status pill + settings gear), restyled into
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

// The Neptune brand mark (trident-on-badge), from the official kit — assets/brand/Neptune -
// Badge.svg, inlined so it can be sized/colored via CSS like the rest of the title bar.
function Badge() {
  return (
    <svg
      className="h-[22px] w-[22px] flex-shrink-0"
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <rect width="48" height="48" rx="12" fill="#144696" />
      <path
        d="M24.0431 9.4054C24.1814 9.52043 25.8385 14.2453 26.0931 14.8784C25.8001 14.8689 25.4807 14.8731 25.1855 14.8712C25.1789 15.0606 25.1761 15.2537 25.1788 15.4444C25.2239 18.6725 25.1758 21.9109 25.2969 25.1347C25.3055 25.2724 25.5911 25.5886 25.7074 25.6273C26.2933 25.7979 27.1424 25.6842 27.7306 25.5373C29.8818 25.0001 29.8492 23.7907 30.0459 22.0302C30.2346 20.3424 30.5574 17.3096 31.4234 15.9195C31.1586 15.8794 30.834 15.8646 30.5618 15.8457C31.4546 14.6834 32.7027 13.7743 34.075 13.2439C34.5085 13.0763 35.1361 12.9476 35.601 12.8414C35.1499 13.2623 34.7064 13.6868 34.3348 14.1922C32.4515 16.7529 32.5611 19.8505 32.2317 22.8588C32.1638 23.6095 32.1318 24.3507 31.998 25.0921C31.6419 27.0657 29.9879 28.003 28.1655 28.3793C27.0354 28.6127 25.6619 28.6533 25.3959 30.049C25.2524 30.8026 25.7651 30.8319 26.1193 31.3646C26.6559 32.1611 26.0092 32.6941 25.3711 33.0951C25.3023 33.5955 25.3102 34.7091 25.2947 35.2543L25.1707 38.5946L23.9734 38.588L23.0447 38.5749L22.8034 33.0798C22.507 32.9055 22.2983 32.8198 22.0816 32.5312C21.9138 32.3103 21.8436 32.0304 21.8876 31.7565C21.9677 31.2314 22.38 30.9456 22.7707 30.6558C22.8963 28.4252 20.8772 28.6854 19.289 28.2318C15.8857 27.2596 16.061 24.9403 15.842 21.99C15.7643 21.067 15.6705 20.1455 15.5607 19.2257C15.2707 16.6837 14.4547 14.4606 12.399 12.827C12.8598 12.9453 13.3501 13.0236 13.8007 13.181C15.4627 13.7616 16.4916 14.5812 17.6276 15.8548C17.3051 15.8636 17.069 15.8656 16.7512 15.919C17.8293 18.0659 17.9339 20.7502 18.2553 23.1088C18.3564 23.8513 18.4777 24.5063 19.14 25.0038C19.8898 25.5671 21.1187 25.735 22.0381 25.6847C22.3948 25.6652 22.6764 25.4137 22.8812 25.1422C22.9453 24.0438 22.9149 22.6917 22.9289 21.5782C22.9721 19.35 22.9948 17.1215 22.9967 14.8929C22.6804 14.8926 22.3292 14.8748 22.0103 14.8657C22.3008 14.3056 22.6161 13.3416 22.8347 12.7286C23.2293 11.6179 23.6321 10.5101 24.0431 9.4054Z"
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
      <Badge />
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

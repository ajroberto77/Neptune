// TitleBar — the app's top brand bar, modeled on the Iridium desktop suite's TitleBar
// (gem mark + mono wordmark + subtitle + backend status pill + settings gear), restyled into
// Neptune's dark "Deep Ocean" palette. Purely presentational.

interface Props {
  /** Backend health / activity, surfaced as the status pill. */
  status?: "ready" | "running" | "error";
  /** Optional override for the pill label. */
  statusText?: string;
  subtitle?: string;
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
  subtitle = "Iridium Capital Management",
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

  return (
    <div className="flex h-12 flex-shrink-0 items-center gap-3 border-b border-ocean-border bg-ocean-panel px-4">
      <Gem />
      <span className="font-mono text-lg font-bold uppercase tracking-[0.2em] text-white">
        Neptune
      </span>
      <span className="h-3.5 w-px bg-white/15" />
      <span className="font-display text-sm font-light text-ocean-muted">{subtitle}</span>
      <div className="flex-1" />
      <span
        className={`rounded-full border px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-wide ${pillCls}`}
        aria-label="backend-status"
      >
        {pill}
      </span>
      <button
        onClick={onSettings}
        title="Settings"
        aria-label="open-settings"
        className="flex h-8 w-8 items-center justify-center rounded text-lg text-ocean-muted transition hover:bg-white/10 hover:text-white"
      >
        ⚙
      </button>
    </div>
  );
}

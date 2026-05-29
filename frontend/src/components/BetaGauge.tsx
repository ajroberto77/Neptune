import type { RiskSummary } from "../types";
import { StatusBadge } from "./StatusBadge";

/** Net-beta monitor: shows the net beta against the +/- tolerance band. */
export function BetaGauge({ summary }: { summary: RiskSummary }) {
  const { net_beta, beta_tol, beta_status, beta_neutral } = summary;
  // Map net beta onto a band scaled to +/- 3x tolerance for readability.
  const span = beta_tol * 3;
  const pct = Math.max(0, Math.min(100, ((net_beta + span) / (2 * span)) * 100));

  return (
    <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
      <div className="flex items-center justify-between">
        <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
          Net Beta
        </h3>
        <StatusBadge status={beta_status} />
      </div>

      <div className="mt-3 font-mono text-3xl font-medium" aria-label="net-beta-value">
        {net_beta >= 0 ? "+" : ""}
        {net_beta.toFixed(4)}
      </div>
      <div className="mt-1 text-xs text-ocean-muted">
        Limit &plusmn;{beta_tol.toFixed(2)} &middot;{" "}
        {beta_neutral ? "beta-neutral" : "OUTSIDE tolerance"}
      </div>

      <div className="relative mt-4 h-2 rounded-full bg-ocean-bg">
        {/* tolerance band */}
        <div
          className="absolute top-0 h-2 rounded-full bg-status-ok/30"
          style={{ left: `${(1 / 3) * 100}%`, width: `${(1 / 3) * 100}%` }}
        />
        {/* current position marker */}
        <div
          className="absolute -top-1 h-4 w-1 rounded bg-ocean-accent"
          style={{ left: `calc(${pct}% - 2px)` }}
          aria-label="net-beta-marker"
        />
      </div>
    </div>
  );
}

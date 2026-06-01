import { useState } from "react";
import type { HedgeProposal } from "../types";
import { money } from "../format";

/** Sector concentration of the proposed short book, with a PM-adjustable limit.
 * This is a soft warning — over-concentrated sectors are flagged, never blocked. */
export function SectorPanel({
  proposal,
  onApplyLimit,
}: {
  proposal: HedgeProposal;
  onApplyLimit: (limit: number) => void;
}) {
  // Limit is stored as a percentage for the input; sent to the API as a fraction.
  const [limitPct, setLimitPct] = useState(Math.round(proposal.sector_limit * 100));

  return (
    <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
      <div className="flex items-center justify-between">
        <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
          Sector Concentration
        </h3>
        <div className="flex items-center gap-2">
          <label className="text-xs text-ocean-muted">Limit</label>
          <input
            type="number"
            min={1}
            max={100}
            value={limitPct}
            onChange={(e) => setLimitPct(Number(e.target.value))}
            aria-label="sector-limit-input"
            className="w-16 rounded border border-ocean-border bg-ocean-bg px-2 py-1 text-right font-mono text-sm"
          />
          <span className="text-xs text-ocean-muted">%</span>
          <button
            onClick={() => onApplyLimit(Math.min(100, Math.max(1, limitPct)) / 100)}
            className="rounded border border-ocean-accent px-3 py-1 text-sm font-medium text-ocean-accent hover:bg-ocean-accent/10"
          >
            Apply
          </button>
        </div>
      </div>

      {proposal.sector_breaches.length > 0 && (
        <p className="mt-3 text-xs text-status-breach">
          {proposal.sector_breaches.length} sector
          {proposal.sector_breaches.length > 1 ? "s" : ""} over the{" "}
          {(proposal.sector_limit * 100).toFixed(0)}% limit:{" "}
          {proposal.sector_breaches.join(", ")} — exploratory capped run; the recommended
          hedge enforces this cap.
        </p>
      )}

      <table className="mt-4 w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase text-ocean-muted">
            <th className="pb-2 font-medium">Sector</th>
            <th className="pb-2 text-right font-medium">Short notional</th>
            <th className="pb-2 font-medium">Share</th>
            <th className="pb-2 text-right font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {proposal.sectors.map((s) => (
            <tr key={s.sector} className="border-t border-ocean-border/60">
              <td className="py-2">{s.sector}</td>
              <td className="py-2 text-right font-mono">{money(s.notional)}</td>
              <td className="py-2">
                <div className="flex items-center gap-2">
                  <div className="h-2 flex-1 rounded-full bg-ocean-bg">
                    <div
                      className={`h-2 rounded-full ${
                        s.breach ? "bg-status-breach" : "bg-ocean-secondary"
                      }`}
                      style={{ width: `${Math.min(100, s.fraction * 100)}%` }}
                    />
                  </div>
                  <span className="w-12 text-right font-mono text-xs">
                    {(s.fraction * 100).toFixed(1)}%
                  </span>
                </div>
              </td>
              <td className="py-2 text-right">
                <span className={s.breach ? "text-status-breach" : "text-status-ok"}>
                  {s.breach ? "BREACH" : "OK"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

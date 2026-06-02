import { useEffect, useState } from "react";
import type { FactorMonitor } from "../types";
import { fetchFactorMonitor } from "../api/client";

const FACTOR_LABELS: Record<string, string> = {
  IVOL: "Idio Vol (low-vol)",
  BAB: "Betting-against-beta",
  AMIHUD: "Illiquidity (Amihud)",
};

/** Report-only monitor for Neptune's price-only factors + per-sector net weight. The PM
 *  watches these; the optimizer does NOT neutralize them and the sector cap remains the
 *  sector control — so this panel is informational, never a breach. */
export function FactorMonitorPanel({ portfolioId }: { portfolioId: string }) {
  const [data, setData] = useState<FactorMonitor | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    fetchFactorMonitor(portfolioId)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(String(e)));
    return () => {
      live = false;
    };
  }, [portfolioId]);

  const factors = data ? Object.entries(data.factors) : [];
  const sectors = data ? Object.entries(data.sectors) : [];

  return (
    <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
      <h3 className="mb-1 font-display text-sm uppercase tracking-wide text-ocean-muted">
        Factor Monitor
      </h3>
      <p className="mb-3 text-xs text-ocean-muted/70">
        Report-only — monitored, not neutralized by the hedge.
      </p>

      {error && <p className="text-sm text-rose-400">{error}</p>}
      {!data && !error && <p className="text-sm text-ocean-muted/60">Loading…</p>}
      {data && !data.available && (
        <p className="text-sm text-ocean-muted/60">
          Not available yet — price-only factor panel not built for this book.
        </p>
      )}

      {data && data.available && (
        <div className="space-y-4">
          {factors.length > 0 && (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-ocean-muted">
                  <th className="pb-2 font-medium">Factor</th>
                  <th className="pb-2 text-right font-medium">Net exposure</th>
                </tr>
              </thead>
              <tbody>
                {factors.map(([f, v]) => (
                  <tr key={f} className="border-t border-ocean-border/60">
                    <td className="py-2">{FACTOR_LABELS[f] ?? f}</td>
                    <td className="py-2 text-right font-mono">{v.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {sectors.length > 0 && (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-ocean-muted">
                  <th className="pb-2 font-medium">Sector</th>
                  <th className="pb-2 text-right font-medium">Net weight</th>
                </tr>
              </thead>
              <tbody>
                {sectors.map(([s, v]) => (
                  <tr key={s} className="border-t border-ocean-border/60">
                    <td className="py-2">{s}</td>
                    <td className="py-2 text-right font-mono">
                      {(v * 100).toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

import type { FactorStatus } from "../types";
import { StatusBadge } from "./StatusBadge";

const FACTOR_LABELS: Record<string, string> = {
  MKT: "Market",
  SMB: "Size (SMB)",
  HML: "Value (HML)",
  RMW: "Profitability (RMW)",
  CMA: "Investment (CMA)",
  MOM: "Momentum",
  // Promoted price-only factors (shown here once a PM neutralizes them).
  IVOL: "Idio Vol",
  BAB: "Betting-against-beta",
  AMIHUD: "Illiquidity",
};

export function FactorTable({ factors }: { factors: FactorStatus[] }) {
  return (
    <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
      <h3 className="mb-3 font-display text-sm uppercase tracking-wide text-ocean-muted">
        Factor Exposures
      </h3>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase text-ocean-muted">
            <th className="pb-2 font-medium">Factor</th>
            <th className="pb-2 text-right font-medium">Exposure</th>
            <th className="pb-2 text-right font-medium">Limit</th>
            <th className="pb-2 text-right font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {factors.map((f) => (
            <tr key={f.factor} className="border-t border-ocean-border/70">
              <td className="py-2">{FACTOR_LABELS[f.factor] ?? f.factor}</td>
              <td className="py-2 text-right font-mono">{f.exposure.toFixed(4)}</td>
              <td className="py-2 text-right font-mono text-ocean-muted">
                &plusmn;{f.limit.toFixed(2)}
              </td>
              <td className="py-2 text-right">
                <StatusBadge status={f.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

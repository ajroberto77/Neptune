import type { RiskSummary } from "../types";
import { BetaGauge } from "../components/BetaGauge";
import { FactorTable } from "../components/FactorTable";
import { FactorMonitorPanel } from "../components/FactorMonitorPanel";

/** Risk tab: net-beta monitor + factor exposure table, plus the report-only price-only factor
 *  monitor. Hedging (propose / review / approve) lives on the Hedge tab. */
export function RiskDashboard({
  summary,
  portfolioId,
}: {
  summary: RiskSummary;
  portfolioId: string;
}) {
  return (
    <div className="space-y-6">
      <p className="text-sm text-ocean-muted">{summary.headline}</p>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <BetaGauge summary={summary} />
        <FactorTable factors={summary.factors} />
      </div>
      <FactorMonitorPanel portfolioId={portfolioId} />
    </div>
  );
}

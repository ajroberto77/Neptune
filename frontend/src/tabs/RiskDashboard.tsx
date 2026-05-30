import type { Frontier, HedgeProposal, RiskSummary } from "../types";
import { BetaGauge } from "../components/BetaGauge";
import { FactorTable } from "../components/FactorTable";
import { FrontierPanel } from "../components/FrontierPanel";
import { money } from "../format";

interface Props {
  summary: RiskSummary;
  proposal: HedgeProposal | null;
  onPropose: () => void;
  proposing: boolean;
  frontier: Frontier | null;
  onFrontier: () => void;
  frontierLoading: boolean;
}

/** Risk tab: net-beta monitor, factor exposure table, and the proposed short basket. */
export function RiskDashboard({
  summary,
  proposal,
  onPropose,
  proposing,
  frontier,
  onFrontier,
  frontierLoading,
}: Props) {
  return (
    <div className="space-y-6">
      <p className="text-sm text-ocean-muted">{summary.headline}</p>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <BetaGauge summary={summary} />
        <FactorTable factors={summary.factors} />
      </div>

      <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
        <div className="flex items-center justify-between">
          <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
            Systematic Hedge Proposal
          </h3>
          <button
            onClick={onPropose}
            disabled={proposing}
            className="rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-ocean-accent/80 disabled:opacity-50"
          >
            {proposing ? "Optimizing…" : "Propose hedge"}
          </button>
        </div>

        {proposal ? (
          <div className="mt-4">
            <div className="flex flex-wrap gap-6 text-sm">
              <Stat label="Net β before" value={proposal.net_beta_before.toFixed(4)} />
              <Stat label="Net β after" value={proposal.net_beta_after.toFixed(4)} />
              <Stat label="Status" value={proposal.status} />
            </div>
            <table className="mt-4 w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-ocean-muted">
                  <th className="pb-2 font-medium">Ticker</th>
                  <th className="pb-2 text-right font-medium">Short Notional</th>
                  <th className="pb-2 text-right font-medium">Beta</th>
                </tr>
              </thead>
              <tbody>
                {proposal.proposed_shorts.map((s) => (
                  <tr key={s.ticker} className="border-t border-ocean-border/60">
                    <td className="py-2 font-mono">{s.ticker}</td>
                    <td className="py-2 text-right font-mono">{money(s.notional)}</td>
                    <td className="py-2 text-right font-mono">{s.beta.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-3 text-xs text-ocean-muted">
              This is a recommendation pending PM approval — Neptune never executes.
            </p>
          </div>
        ) : (
          <p className="mt-4 text-sm text-ocean-muted">
            No proposal yet. Run the optimizer to neutralize residual beta.
          </p>
        )}
      </div>

      <FrontierPanel frontier={frontier} onRun={onFrontier} loading={frontierLoading} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase text-ocean-muted">{label}</div>
      <div className="font-mono text-lg">{value}</div>
    </div>
  );
}

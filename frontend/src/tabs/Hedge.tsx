import { useState } from "react";
import type { Frontier, HedgeProposal } from "../types";
import { SectorPanel } from "../components/SectorPanel";
import { FrontierPanel } from "../components/FrontierPanel";
import { money, signedMoney } from "../format";

interface Props {
  proposal: HedgeProposal | null;
  portfolios: { id: string; name: string }[];
  hedgePortfolioId: string;
  onHedgePortfolio: (id: string) => void;
  onPropose: (sectorLimit?: number, maxNames?: number) => void;
  proposing: boolean;
  onApprove: () => void;
  onReject: () => void;
  approving: boolean;
  frontier: Frontier | null;
  onFrontier: () => void;
  frontierLoading: boolean;
}

const shares = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 0 });

/** Hedge tab: set the sector cap and target basket size in the proposal header, propose a
 *  diversified systematic short basket, then approve/reject the WHOLE basket. Approve books the
 *  names as systematic shorts (never the discretionary path — I-03), recorded, never routed
 *  (I-01). The sector-concentration breakdown shows below once a basket is proposed. */
export function Hedge({
  proposal,
  portfolios,
  hedgePortfolioId,
  onHedgePortfolio,
  onPropose,
  proposing,
  onApprove,
  onReject,
  approving,
  frontier,
  onFrontier,
  frontierLoading,
}: Props) {
  const [sectorLimitPct, setSectorLimitPct] = useState(30);
  const [targetShorts, setTargetShorts] = useState<string>("");

  function propose() {
    onPropose(
      Math.min(100, Math.max(1, sectorLimitPct)) / 100,
      targetShorts ? Number(targetShorts) : undefined,
    );
  }

  const totalNotional = (proposal?.proposed_shorts ?? []).reduce((a, s) => a + s.notional, 0);
  const totalBetaAdj = (proposal?.proposed_shorts ?? []).reduce(
    (a, s) => a + s.notional * s.beta,
    0,
  );
  const wtdAvgBeta = totalNotional ? totalBetaAdj / totalNotional : 0;

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
            Systematic Hedge Proposal
          </h3>
          <div className="flex items-end gap-3">
            <label className="text-xs text-ocean-muted">
              <span className="mb-1 block">Portfolio</span>
              <select
                className="np-input py-1"
                value={hedgePortfolioId}
                onChange={(e) => onHedgePortfolio(e.target.value)}
              >
                {portfolios.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs text-ocean-muted">
              <span className="mb-1 block">Sector limit %</span>
              <input
                type="number"
                min={1}
                max={100}
                value={sectorLimitPct}
                aria-label="sector-limit-input"
                onChange={(e) => setSectorLimitPct(Number(e.target.value))}
                className="np-input w-20"
              />
            </label>
            <label className="text-xs text-ocean-muted">
              <span className="mb-1 block">Target shorts</span>
              <input
                type="number"
                min={1}
                placeholder="35"
                value={targetShorts}
                onChange={(e) => setTargetShorts(e.target.value)}
                className="np-input w-24"
              />
            </label>
            <button
              onClick={propose}
              disabled={proposing}
              className="rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-ocean-accent/80 disabled:opacity-50"
            >
              {proposing ? "Optimizing…" : "Propose hedge"}
            </button>
          </div>
        </div>

        {proposal ? (
          <div className="mt-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-6 text-sm">
                <Stat label="Net β before" value={proposal.net_beta_before.toFixed(4)} />
                <Stat label="Net β after" value={proposal.net_beta_after.toFixed(4)} />
              </div>
              <div className="flex gap-2">
                <button
                  onClick={onApprove}
                  disabled={approving}
                  className="rounded bg-status-ok/80 px-3 py-1.5 text-sm font-medium text-white hover:bg-status-ok disabled:opacity-50"
                >
                  {approving ? "Booking…" : "Approve basket"}
                </button>
                <button
                  onClick={onReject}
                  disabled={approving}
                  className="rounded border border-status-breach/50 px-3 py-1.5 text-sm font-medium text-status-breach hover:bg-status-breach/10 disabled:opacity-50"
                >
                  Reject
                </button>
              </div>
            </div>

            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-ocean-muted">
                  <th className="pb-2 font-medium">Ticker</th>
                  <th className="pb-2 font-medium">Sector</th>
                  <th className="pb-2 text-right font-medium">Shares</th>
                  <th className="pb-2 text-right font-medium">Short Notional</th>
                  <th className="pb-2 text-right font-medium">Beta</th>
                  <th className="pb-2 text-right font-medium">Beta-Adj Notional</th>
                </tr>
              </thead>
              <tbody>
                {proposal.proposed_shorts.map((s) => (
                  <tr key={s.ticker} className="border-t border-ocean-border/60">
                    <td className="py-2 font-mono">{s.ticker}</td>
                    <td className="py-2 text-ocean-muted">{s.sector ?? "—"}</td>
                    <td className="py-2 text-right font-mono">
                      {s.shares == null ? "—" : shares(s.shares)}
                    </td>
                    <td className="py-2 text-right font-mono">{money(s.notional)}</td>
                    <td className="py-2 text-right font-mono">{s.beta.toFixed(2)}</td>
                    <td className="py-2 text-right font-mono">{money(s.notional * s.beta)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-ocean-border font-medium">
                  <td className="py-2 text-xs uppercase text-ocean-muted">Total</td>
                  <td></td>
                  <td></td>
                  <td className="py-2 text-right font-mono">{money(totalNotional)}</td>
                  <td className="py-2 text-right font-mono" title="weighted-average beta">
                    {wtdAvgBeta.toFixed(2)}
                  </td>
                  <td className="py-2 text-right font-mono">{signedMoney(totalBetaAdj)}</td>
                </tr>
              </tfoot>
            </table>
            <p className="mt-3 text-xs text-ocean-muted">
              Weighted-average beta = beta-adjusted short notional ÷ short notional. Approving
              books these as systematic shorts (recorded for the human workflow, never routed).
            </p>
          </div>
        ) : (
          <p className="mt-4 text-sm text-ocean-muted">
            No proposal yet. Run the optimizer for a diversified systematic short basket that
            neutralizes residual beta.
          </p>
        )}
      </div>

      {proposal && <SectorPanel proposal={proposal} />}

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

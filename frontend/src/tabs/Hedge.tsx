import { useState } from "react";
import type { Frontier, HedgeProposal } from "../types";
import { SectorPanel } from "../components/SectorPanel";
import { FrontierPanel } from "../components/FrontierPanel";
import { money, signedMoney } from "../format";

interface Props {
  proposal: HedgeProposal | null;
  onPropose: (sectorLimit?: number, maxNames?: number) => void;
  proposing: boolean;
  onApplySectorLimit: (limit: number) => void;
  onApprove: () => void;
  onReject: () => void;
  approving: boolean;
  frontier: Frontier | null;
  onFrontier: () => void;
  frontierLoading: boolean;
}

const shares = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 0 });

/** Hedge tab: set the sector cap (always visible, top), propose a diversified systematic short
 *  basket, review it, and approve/reject the WHOLE basket. Approve books the names as
 *  systematic shorts (never the discretionary path — I-03) and records the decision; Neptune
 *  never routes orders (I-01). */
export function Hedge({
  proposal,
  onPropose,
  proposing,
  onApplySectorLimit,
  onApprove,
  onReject,
  approving,
  frontier,
  onFrontier,
  frontierLoading,
}: Props) {
  const [maxNames, setMaxNames] = useState<string>("");

  // Totals for the proposal footer.
  const totalNotional = (proposal?.proposed_shorts ?? []).reduce((a, s) => a + s.notional, 0);
  const totalBetaAdj = (proposal?.proposed_shorts ?? []).reduce(
    (a, s) => a + s.notional * s.beta,
    0,
  );
  const wtdAvgBeta = totalNotional ? totalBetaAdj / totalNotional : 0;

  return (
    <div className="space-y-6">
      {/* Sector concentration cap — always visible, enforced on every proposed hedge. */}
      <SectorPanel proposal={proposal} onApplyLimit={onApplySectorLimit} />

      <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
            Systematic Hedge Proposal
          </h3>
          <div className="flex items-end gap-3">
            <label className="text-xs text-ocean-muted">
              <span className="mb-1 block">Target shorts</span>
              <input
                type="number"
                min={1}
                placeholder="35"
                value={maxNames}
                onChange={(e) => setMaxNames(e.target.value)}
                className="np-input w-24"
              />
            </label>
            <button
              onClick={() => onPropose(undefined, maxNames ? Number(maxNames) : undefined)}
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
                <span className="rounded border border-status-watch/40 bg-status-watch/15 px-2 py-0.5 font-mono text-xs text-status-watch">
                  {proposal.status}
                </span>
              </div>
              {/* Approve / reject the WHOLE basket. */}
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

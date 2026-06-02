import type { Frontier, HedgeProposal } from "../types";
import { SectorPanel } from "../components/SectorPanel";
import { FrontierPanel } from "../components/FrontierPanel";
import { money } from "../format";

import { useState } from "react";

interface Props {
  proposal: HedgeProposal | null;
  onPropose: (sectorLimit?: number, maxNames?: number) => void;
  proposing: boolean;
  onApplySectorLimit: (limit: number) => void;
  frontier: Frontier | null;
  onFrontier: () => void;
  frontierLoading: boolean;
}

/** Hedge tab: propose the systematic short basket, review it, and approve/reject — one
 *  workflow end to end. Neptune recommends; a human approves. There is NO execution pathway:
 *  approvals are recorded for a human workflow, never routed to a broker (invariant I-01). */
export function Hedge({
  proposal,
  onPropose,
  proposing,
  onApplySectorLimit,
  frontier,
  onFrontier,
  frontierLoading,
}: Props) {
  // Blank = the natural sparse basket (L1 gross penalty picks the few efficient names).
  const [maxNames, setMaxNames] = useState<string>("");

  return (
    <div className="space-y-6">
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
            <div className="flex flex-wrap items-center gap-6 text-sm">
              <Stat label="Net β before" value={proposal.net_beta_before.toFixed(4)} />
              <Stat label="Net β after" value={proposal.net_beta_after.toFixed(4)} />
              <span className="rounded border border-status-watch/40 bg-status-watch/15 px-2 py-0.5 font-mono text-xs text-status-watch">
                {proposal.status}
              </span>
            </div>
            <table className="mt-4 w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-ocean-muted">
                  <th className="pb-2 font-medium">Ticker</th>
                  <th className="pb-2 font-medium">Sector</th>
                  <th className="pb-2 text-right font-medium">Short Notional</th>
                  <th className="pb-2 text-right font-medium">Beta</th>
                  <th className="pb-2 text-right font-medium">Decision</th>
                </tr>
              </thead>
              <tbody>
                {proposal.proposed_shorts.map((s) => (
                  <tr key={s.ticker} className="border-t border-ocean-border/60">
                    <td className="py-2 font-mono">{s.ticker}</td>
                    <td className="py-2 text-ocean-muted">{s.sector ?? "—"}</td>
                    <td className="py-2 text-right font-mono">{money(s.notional)}</td>
                    <td className="py-2 text-right font-mono">{s.beta.toFixed(2)}</td>
                    <td className="py-2 text-right">
                      <div className="inline-flex gap-2">
                        <button className="rounded border border-status-ok/40 px-2 py-0.5 text-xs text-status-ok hover:bg-status-ok/10">
                          Approve
                        </button>
                        <button className="rounded border border-status-breach/40 px-2 py-0.5 text-xs text-status-breach hover:bg-status-breach/10">
                          Reject
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-3 text-xs text-ocean-muted">
              This basket is a recommendation pending PM approval — Neptune records the decision
              for a human workflow and never routes orders.
            </p>
          </div>
        ) : (
          <p className="mt-4 text-sm text-ocean-muted">
            No proposal yet. Run the optimizer to neutralize residual beta with a systematic
            short basket.
          </p>
        )}
      </div>

      {proposal && <SectorPanel proposal={proposal} onApplyLimit={onApplySectorLimit} />}

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

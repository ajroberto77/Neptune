import type { HedgeProposal } from "../types";
import { money } from "../format";

/** Hedge approval tab: a PM reviews the optimizer's proposal and approves/rejects.
 * The buttons are advisory UI only — Neptune has no execution pathway (I-01). */
export function HedgeApproval({ proposal }: { proposal: HedgeProposal | null }) {
  if (!proposal) {
    return (
      <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5 text-sm text-ocean-muted">
        No pending hedge proposal. Generate one from the Risk Dashboard.
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
      <div className="flex items-center justify-between">
        <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
          Pending Approval
        </h3>
        <span className="rounded border border-status-watch/40 bg-status-watch/15 px-2 py-0.5 font-mono text-xs text-status-watch">
          {proposal.status}
        </span>
      </div>

      <table className="mt-4 w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase text-ocean-muted">
            <th className="pb-2 font-medium">Ticker</th>
            <th className="pb-2 text-right font-medium">Short Notional</th>
            <th className="pb-2 text-right font-medium">Beta</th>
            <th className="pb-2 text-right font-medium">Decision</th>
          </tr>
        </thead>
        <tbody>
          {proposal.proposed_shorts.map((s) => (
            <tr key={s.ticker} className="border-t border-ocean-border/60">
              <td className="py-2 font-mono">{s.ticker}</td>
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
        Approvals are recorded for a human workflow — the system does not route orders.
      </p>
    </div>
  );
}

import type { PositionRow } from "../types";
import { money, pnlColor, price, signedMoney } from "../format";

const BOOKS: { title: string; book: string }[] = [
  { title: "Long Book", book: "LONG" },
  { title: "Systematic Short", book: "SYSTEMATIC_SHORT" },
  { title: "Discretionary Short", book: "DISCRETIONARY_SHORT" },
];

/** Portfolio view: long / systematic short / discretionary short sub-panels with the four
 * P&L dimensions. Systematic and discretionary shorts are kept separate (invariant I-03).
 * Optional live-pricing control polls the latest prices on a configurable interval. */
export function Portfolio({
  positions,
  refreshMins,
  onChangeMins,
  onRefreshNow,
  lastPriced,
  pricing,
}: {
  positions: PositionRow[];
  refreshMins?: number;
  onChangeMins?: (m: number) => void;
  onRefreshNow?: () => void;
  lastPriced?: string | null;
  pricing?: boolean;
}) {
  return (
    <div className="space-y-6">
      {onRefreshNow && (
        <div className="flex items-center justify-end gap-3 text-sm text-ocean-muted">
          <span>Server price refresh: every</span>
          <input
            type="number"
            min={0}
            value={refreshMins ?? 0}
            onChange={(e) => onChangeMins?.(Number(e.target.value))}
            className="np-input w-16 text-right"
          />
          <span>min {refreshMins ? "" : "(off)"}</span>
          <button
            onClick={onRefreshNow}
            disabled={pricing}
            className="rounded border border-ocean-border px-3 py-1.5 hover:text-slate-200 disabled:opacity-50"
          >
            {pricing ? "Refreshing…" : "Refresh now"}
          </button>
          {lastPriced && <span className="text-xs text-ocean-muted/60">updated {lastPriced}</span>}
        </div>
      )}
      {BOOKS.map(({ title, book }) => {
        const rows = positions.filter((p) => p.book === book);
        return (
          <div
            key={book}
            className="rounded-lg border border-ocean-border bg-ocean-panel p-5"
          >
            <h3 className="mb-3 font-display text-sm uppercase tracking-wide text-ocean-muted">
              {title} <span className="text-ocean-muted/60">({rows.length})</span>
            </h3>
            {rows.length === 0 ? (
              <p className="text-sm text-ocean-muted">No positions.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-ocean-muted">
                    <th className="pb-2 font-medium">Ticker</th>
                    <th className="pb-2 text-right font-medium">Beta</th>
                    <th className="pb-2 text-right font-medium">Price</th>
                    <th className="pb-2 text-right font-medium">Notional</th>
                    <th className="pb-2 text-right font-medium">Day P&L</th>
                    <th className="pb-2 text-right font-medium">Unrealised</th>
                    <th className="pb-2 text-right font-medium">Realised</th>
                    <th className="pb-2 text-right font-medium">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((p) => (
                    <tr key={p.ticker} className="border-t border-ocean-border/60">
                      <td className="py-2 font-mono">
                        {p.ticker}
                        <span className="ml-2 text-xs text-ocean-muted/60">
                          {p.beta_method === "forward_override" ? "ovr" : "live"}
                        </span>
                      </td>
                      <td className="py-2 text-right font-mono">{p.beta.toFixed(2)}</td>
                      <td className="py-2 text-right font-mono">{price(p.price)}</td>
                      <td className="py-2 text-right font-mono">{money(p.notional)}</td>
                      <td className={`py-2 text-right font-mono ${pnlColor(p.pnl.day)}`}>
                        {signedMoney(p.pnl.day)}
                      </td>
                      <td className={`py-2 text-right font-mono ${pnlColor(p.pnl.unrealised)}`}>
                        {signedMoney(p.pnl.unrealised)}
                      </td>
                      <td className={`py-2 text-right font-mono ${pnlColor(p.pnl.realised)}`}>
                        {signedMoney(p.pnl.realised)}
                      </td>
                      <td className={`py-2 text-right font-mono ${pnlColor(p.pnl.total)}`}>
                        {signedMoney(p.pnl.total)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        );
      })}
    </div>
  );
}

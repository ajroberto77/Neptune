import type { PositionRow } from "../types";
import { money, pnlColor, signedMoney } from "../format";

const BOOKS: { title: string; book: string }[] = [
  { title: "Long Book", book: "LONG" },
  { title: "Systematic Short", book: "SYSTEMATIC_SHORT" },
  { title: "Discretionary Short", book: "DISCRETIONARY_SHORT" },
];

/** Live blotter: long / systematic short / discretionary short sub-panels with the four
 * P&L dimensions. Systematic and discretionary shorts are kept separate (invariant I-03). */
export function Blotter({ positions }: { positions: PositionRow[] }) {
  return (
    <div className="space-y-6">
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

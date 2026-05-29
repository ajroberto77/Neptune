import type { PositionRow } from "../types";
import { money } from "../format";

const BOOKS: { title: string; match: (p: PositionRow) => boolean }[] = [
  { title: "Long Book", match: (p) => p.side === "LONG" },
  { title: "Systematic Short", match: (p) => p.short_type === "SYSTEMATIC" },
  { title: "Discretionary Short", match: (p) => p.short_type === "DISCRETIONARY" },
];

/** Live blotter: long / systematic short / discretionary short sub-panels.
 * Systematic and discretionary shorts are kept separate (invariant I-03). */
export function Blotter({ positions }: { positions: PositionRow[] }) {
  return (
    <div className="space-y-6">
      {BOOKS.map((book) => {
        const rows = positions.filter(book.match);
        return (
          <div
            key={book.title}
            className="rounded-lg border border-ocean-border bg-ocean-panel p-5"
          >
            <h3 className="mb-3 font-display text-sm uppercase tracking-wide text-ocean-muted">
              {book.title}{" "}
              <span className="text-ocean-muted/60">({rows.length})</span>
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
                  </tr>
                </thead>
                <tbody>
                  {rows.map((p) => (
                    <tr key={p.ticker} className="border-t border-ocean-border/60">
                      <td className="py-2 font-mono">{p.ticker}</td>
                      <td className="py-2 text-right font-mono">{p.beta.toFixed(2)}</td>
                      <td className="py-2 text-right font-mono">{money(p.notional)}</td>
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

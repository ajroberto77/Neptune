import type { PositionRow } from "../types";
import { money, pnlColor, price, signedMoney } from "../format";

const SECTIONS: { title: string; total: string; side: string }[] = [
  { title: "Longs", total: "Total Long", side: "LONG" },
  { title: "Shorts", total: "Total Short", side: "SHORT" },
];

// Signed exposure: longs +, shorts −. Beta-adjusted notional = signed notional × beta, so a
// beta-neutral book nets to ~0 (longs and shorts offset).
const sign = (p: PositionRow) => (p.side === "SHORT" ? -1 : 1);
const betaAdj = (p: PositionRow) => sign(p) * p.notional * p.beta;

interface Totals {
  notional: number; // gross notional in the section
  signedNotional: number; // longs − shorts
  betaAdj: number;
  day: number;
  unrealized: number;
  realized: number;
  total: number;
}

function sumTotals(rows: PositionRow[]): Totals {
  return rows.reduce<Totals>(
    (a, p) => ({
      notional: a.notional + p.notional,
      signedNotional: a.signedNotional + sign(p) * p.notional,
      betaAdj: a.betaAdj + betaAdj(p),
      day: a.day + p.pnl.day,
      unrealized: a.unrealized + p.pnl.unrealized,
      realized: a.realized + p.pnl.realized,
      total: a.total + p.pnl.total,
    }),
    { notional: 0, signedNotional: 0, betaAdj: 0, day: 0, unrealized: 0, realized: 0, total: 0 },
  );
}

/** Portfolio view: the book IS the portfolio. Positions are grouped into Longs and Shorts;
 * each short carries a systematic-vs-discretionary tag (systematic = optimizer hedge), kept
 * distinct per invariant I-03. Each row and the totals show beta-adjusted notional, so the Net
 * Position's beta-adjusted exposure is ~0 when the book is hedged to zero net beta. */
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
  const longs = positions.filter((p) => p.side === "LONG" && p.notional !== 0);
  const shorts = positions.filter((p) => p.side === "SHORT" && p.notional !== 0);
  const net = sumTotals([...longs, ...shorts]);

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

      {SECTIONS.map(({ title, total, side }) => {
        const rows = side === "LONG" ? longs : shorts;
        const t = sumTotals(rows);
        return (
          <div key={side} className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
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
                    <th className="pb-2 text-right font-medium">Price</th>
                    <th className="pb-2 text-right font-medium">Notional</th>
                    <th className="pb-2 text-right font-medium">Beta-Adj Notional</th>
                    <th className="pb-2 text-right font-medium">Day P&L</th>
                    <th className="pb-2 text-right font-medium">Unrealized</th>
                    <th className="pb-2 text-right font-medium">Realized</th>
                    <th className="pb-2 text-right font-medium">Total</th>
                    <th className="pb-2 text-right font-medium">Beta</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((p) => (
                    <tr key={p.ticker} className="border-t border-ocean-border/60">
                      <td className="py-2 font-mono">
                        {p.ticker}
                        {p.side === "SHORT" && (
                          <span
                            className={`ml-2 rounded px-1.5 py-0.5 text-[10px] uppercase ${
                              p.short_type === "SYSTEMATIC"
                                ? "bg-ocean-accent/20 text-ocean-accent"
                                : "bg-ocean-border/60 text-ocean-muted"
                            }`}
                          >
                            {p.short_type === "SYSTEMATIC" ? "systematic" : "discretionary"}
                          </span>
                        )}
                        <span className="ml-2 text-xs text-ocean-muted/60">
                          {p.beta_method === "forward_override"
                            ? "ovr"
                            : p.beta_method === "insufficient_data"
                              ? "thin"
                              : "live"}
                        </span>
                      </td>
                      <td className="py-2 text-right font-mono">
                        {p.price == null ? "—" : price(p.price)}
                      </td>
                      <td className="py-2 text-right font-mono">{money(p.notional)}</td>
                      <td className="py-2 text-right font-mono">{signedMoney(betaAdj(p))}</td>
                      <td className={`py-2 text-right font-mono ${pnlColor(p.pnl.day)}`}>
                        {signedMoney(p.pnl.day)}
                      </td>
                      <td className={`py-2 text-right font-mono ${pnlColor(p.pnl.unrealized)}`}>
                        {signedMoney(p.pnl.unrealized)}
                      </td>
                      <td className={`py-2 text-right font-mono ${pnlColor(p.pnl.realized)}`}>
                        {signedMoney(p.pnl.realized)}
                      </td>
                      <td className={`py-2 text-right font-mono ${pnlColor(p.pnl.total)}`}>
                        {signedMoney(p.pnl.total)}
                      </td>
                      <td className="py-2 text-right font-mono">{p.beta.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t-2 border-ocean-border font-medium">
                    <td className="py-2 text-xs uppercase text-ocean-muted">{total}</td>
                    <td></td>
                    <td className="py-2 text-right font-mono">{money(t.notional)}</td>
                    <td className="py-2 text-right font-mono">{signedMoney(t.betaAdj)}</td>
                    <td className={`py-2 text-right font-mono ${pnlColor(t.day)}`}>{signedMoney(t.day)}</td>
                    <td className={`py-2 text-right font-mono ${pnlColor(t.unrealized)}`}>{signedMoney(t.unrealized)}</td>
                    <td className={`py-2 text-right font-mono ${pnlColor(t.realized)}`}>{signedMoney(t.realized)}</td>
                    <td className={`py-2 text-right font-mono ${pnlColor(t.total)}`}>{signedMoney(t.total)}</td>
                    <td></td>
                  </tr>
                </tfoot>
              </table>
            )}
          </div>
        );
      })}

      {/* Net position: long − short. Net beta-adjusted notional ~0 means the book is hedged to
          zero net beta. */}
      <div className="rounded-lg border border-ocean-accent/40 bg-ocean-panel p-5">
        <h3 className="mb-3 font-display text-sm uppercase tracking-wide text-ocean-accent">
          Net Position
        </h3>
        <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
          <Stat label="Net Notional" value={signedMoney(net.signedNotional)} />
          <Stat
            label="Net Beta-Adj Notional"
            value={signedMoney(net.betaAdj)}
            hint="≈ $0 when hedged to zero net beta"
          />
          <Stat label="Net Day P&L" value={signedMoney(net.day)} color={pnlColor(net.day)} />
          <Stat label="Net Total P&L" value={signedMoney(net.total)} color={pnlColor(net.total)} />
        </div>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
  color,
}: {
  label: string;
  value: string;
  hint?: string;
  color?: string;
}) {
  return (
    <div>
      <div className="text-xs uppercase text-ocean-muted">{label}</div>
      <div className={`font-mono text-lg ${color ?? "text-slate-100"}`}>{value}</div>
      {hint && <div className="text-[11px] text-ocean-muted/70">{hint}</div>}
    </div>
  );
}

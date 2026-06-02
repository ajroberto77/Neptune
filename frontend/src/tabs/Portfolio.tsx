import type { PositionRow } from "../types";
import { money, pnlColor, price, signedMoney } from "../format";

// Signed exposure: longs +, shorts −. Beta-adjusted notional = signed notional × beta, so a
// beta-neutral book nets to ~0 (longs and shorts offset).
const sign = (p: PositionRow) => (p.side === "SHORT" ? -1 : 1);
const betaAdj = (p: PositionRow) => sign(p) * p.notional * p.beta;

interface Totals {
  notional: number; // gross notional in the section
  signedNotional: number; // longs − shorts
  betaAdj: number; // signed Σ sign·notional·beta
  betaNotional: number; // Σ notional·beta (name beta) — for the weighted-average beta
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
      betaNotional: a.betaNotional + p.notional * p.beta,
      day: a.day + p.pnl.day,
      unrealized: a.unrealized + p.pnl.unrealized,
      realized: a.realized + p.pnl.realized,
      total: a.total + p.pnl.total,
    }),
    { notional: 0, signedNotional: 0, betaAdj: 0, betaNotional: 0, day: 0, unrealized: 0, realized: 0, total: 0 },
  );
}

// One shared column layout so the Net, Longs, and Shorts tables line up column-for-column.
function Cols() {
  const widths = ["18%", "8%", "8%", "11%", "13%", "10%", "10%", "7%", "10%", "5%"];
  return (
    <colgroup>
      {widths.map((w, i) => (
        <col key={i} style={{ width: w }} />
      ))}
    </colgroup>
  );
}

function Head() {
  return (
    <thead>
      <tr className="text-left text-xs uppercase text-ocean-muted">
        <th className="pb-2 font-medium">Ticker</th>
        <th className="pb-2 text-right font-medium">Price</th>
        <th className="pb-2 text-right font-medium">Shares</th>
        <th className="pb-2 text-right font-medium">Notional</th>
        <th className="pb-2 text-right font-medium">Beta-Adj Notional</th>
        <th className="pb-2 text-right font-medium">Day P&L</th>
        <th className="pb-2 text-right font-medium">Unrealized</th>
        <th className="pb-2 text-right font-medium">Realized</th>
        <th className="pb-2 text-right font-medium">Total</th>
        <th className="pb-2 text-right font-medium">Beta</th>
      </tr>
    </thead>
  );
}

/** A bold totals/net row using the shared columns. ``beta`` is the notional-weighted average
 *  beta; ``notional`` is shown as given (gross for a book, net for the Net row). */
function TotalsRow({
  label,
  notional,
  betaAdj: ba,
  t,
  beta,
  subtle = false,
}: {
  label: string;
  notional: number;
  betaAdj: number;
  t: Totals;
  beta: number;
  subtle?: boolean; // a subgroup subtotal (lighter) vs a section/grand total (bold rule)
}) {
  return (
    <tr
      className={
        subtle
          ? "border-t border-ocean-border/60 font-medium text-ocean-muted"
          : "border-t-2 border-ocean-border font-medium"
      }
    >
      <td className="py-2 text-xs uppercase text-ocean-muted">{label}</td>
      <td></td>
      <td></td>
      <td className="py-2 text-right font-mono">{label.startsWith("Net") ? signedMoney(notional) : money(notional)}</td>
      <td className="py-2 text-right font-mono">{signedMoney(ba)}</td>
      <td className={`py-2 text-right font-mono ${pnlColor(t.day)}`}>{signedMoney(t.day)}</td>
      <td className={`py-2 text-right font-mono ${pnlColor(t.unrealized)}`}>{signedMoney(t.unrealized)}</td>
      <td className={`py-2 text-right font-mono ${pnlColor(t.realized)}`}>{signedMoney(t.realized)}</td>
      <td className={`py-2 text-right font-mono ${pnlColor(t.total)}`}>{signedMoney(t.total)}</td>
      <td className="py-2 text-right font-mono">{Number.isFinite(beta) ? beta.toFixed(2) : "—"}</td>
    </tr>
  );
}

// A non-standard beta source flagged inline; a normal pipeline beta shows nothing (no clutter).
function betaFlag(p: PositionRow) {
  if (p.beta_method === "forward_override")
    return <span className="ml-2 text-[10px] uppercase text-ocean-muted/60">ovr</span>;
  if (p.beta_method === "insufficient_data")
    return <span className="ml-2 text-[10px] uppercase text-status-watch">thin</span>;
  return null;
}

/** One position row, shared by Longs and both Shorts subgroups so every table lines up. */
function PositionTr({ p }: { p: PositionRow }) {
  return (
    <tr className="border-t border-ocean-border/60">
      <td className="truncate py-2 font-mono">
        {p.ticker}
        {betaFlag(p)}
      </td>
      <td className="py-2 text-right font-mono">{p.price == null ? "—" : price(p.price)}</td>
      <td className="py-2 text-right font-mono">
        {p.quantity ? p.quantity.toLocaleString(undefined, { maximumFractionDigits: 0 }) : "—"}
      </td>
      <td className="py-2 text-right font-mono">{money(p.notional)}</td>
      <td className="py-2 text-right font-mono">{signedMoney(betaAdj(p))}</td>
      <td className={`py-2 text-right font-mono ${pnlColor(p.pnl.day)}`}>{signedMoney(p.pnl.day)}</td>
      <td className={`py-2 text-right font-mono ${pnlColor(p.pnl.unrealized)}`}>{signedMoney(p.pnl.unrealized)}</td>
      <td className={`py-2 text-right font-mono ${pnlColor(p.pnl.realized)}`}>{signedMoney(p.pnl.realized)}</td>
      <td className={`py-2 text-right font-mono ${pnlColor(p.pnl.total)}`}>{signedMoney(p.pnl.total)}</td>
      <td className="py-2 text-right font-mono">{p.beta.toFixed(2)}</td>
    </tr>
  );
}

/** A subgroup header row (e.g. "Systematic Shorts") spanning the shared columns. */
function SubHeader({ label, count }: { label: string; count: number }) {
  return (
    <tr>
      <td colSpan={10} className="pt-4 pb-1 text-xs font-medium uppercase tracking-wide text-ocean-muted">
        {label} <span className="text-ocean-muted/60">({count})</span>
      </td>
    </tr>
  );
}

const wtdBetaOf = (t: Totals) => (t.notional ? t.betaNotional / t.notional : NaN);

/** Portfolio view: a Net Position summary on top, then Longs and Shorts — all sharing one column
 *  layout so they read top-to-bottom. The Shorts table is split into Systematic and (if any)
 *  Discretionary subgroups, each with its own subtotal, then the grand Total Short — so the two
 *  books stay distinct (I-03) without per-row tags. Net beta-adj is ~0 when hedged to zero beta. */
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
  const netBeta = net.signedNotional ? net.betaAdj / net.signedNotional : NaN;

  // Shorts split into the systematic hedge book and the discretionary book (I-03), each subtotalled.
  const sysShorts = shorts.filter((p) => p.short_type === "SYSTEMATIC");
  const discShorts = shorts.filter((p) => p.short_type !== "SYSTEMATIC");
  const longTot = sumTotals(longs);
  const shortTot = sumTotals(shorts);
  const sysTot = sumTotals(sysShorts);
  const discTot = sumTotals(discShorts);
  const bothShortBooks = sysShorts.length > 0 && discShorts.length > 0;

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

      {/* Net Position on top — long − short. Net beta-adj ~0 means hedged to zero net beta. */}
      <div className="rounded-lg border border-ocean-accent/40 bg-ocean-panel p-5">
        <h3 className="mb-3 font-display text-sm uppercase tracking-wide text-ocean-accent">
          Net Position
        </h3>
        <table className="w-full table-fixed text-sm">
          <Cols />
          <Head />
          <tbody>
            <TotalsRow label="Net" notional={net.signedNotional} betaAdj={net.betaAdj} t={net} beta={netBeta} />
          </tbody>
        </table>
      </div>

      {/* Longs */}
      <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
        <h3 className="mb-3 font-display text-sm uppercase tracking-wide text-ocean-muted">
          Longs <span className="text-ocean-muted/60">({longs.length})</span>
        </h3>
        {longs.length === 0 ? (
          <p className="text-sm text-ocean-muted">No positions.</p>
        ) : (
          <table className="w-full table-fixed text-sm">
            <Cols />
            <Head />
            <tbody>
              {longs.map((p) => (
                <PositionTr key={p.ticker} p={p} />
              ))}
            </tbody>
            <tfoot>
              <TotalsRow label="Total Long" notional={longTot.notional} betaAdj={longTot.betaAdj}
                         t={longTot} beta={wtdBetaOf(longTot)} />
            </tfoot>
          </table>
        )}
      </div>

      {/* Shorts — Systematic and (if any) Discretionary subgroups, then the grand Total Short */}
      <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
        <h3 className="mb-3 font-display text-sm uppercase tracking-wide text-ocean-muted">
          Shorts <span className="text-ocean-muted/60">({shorts.length})</span>
        </h3>
        {shorts.length === 0 ? (
          <p className="text-sm text-ocean-muted">No positions.</p>
        ) : (
          <table className="w-full table-fixed text-sm">
            <Cols />
            <Head />
            <tbody>
              {sysShorts.length > 0 && (
                <>
                  <SubHeader label="Systematic Shorts" count={sysShorts.length} />
                  {sysShorts.map((p) => (
                    <PositionTr key={p.ticker} p={p} />
                  ))}
                  {bothShortBooks && (
                    <TotalsRow subtle label="Total Systematic" notional={sysTot.notional}
                               betaAdj={sysTot.betaAdj} t={sysTot} beta={wtdBetaOf(sysTot)} />
                  )}
                </>
              )}
              {discShorts.length > 0 && (
                <>
                  <SubHeader label="Discretionary Shorts" count={discShorts.length} />
                  {discShorts.map((p) => (
                    <PositionTr key={p.ticker} p={p} />
                  ))}
                  {bothShortBooks && (
                    <TotalsRow subtle label="Total Discretionary" notional={discTot.notional}
                               betaAdj={discTot.betaAdj} t={discTot} beta={wtdBetaOf(discTot)} />
                  )}
                </>
              )}
            </tbody>
            <tfoot>
              <TotalsRow label="Total Short" notional={shortTot.notional} betaAdj={shortTot.betaAdj}
                         t={shortTot} beta={wtdBetaOf(shortTot)} />
            </tfoot>
          </table>
        )}
      </div>
    </div>
  );
}

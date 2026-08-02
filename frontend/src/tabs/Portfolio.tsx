import type { PositionRow } from "../types";
import { money, pnlColor, price, signedMoney, signedPct } from "../format";

// Signed exposure: longs +, shorts −. Beta-adjusted notional = signed notional × beta, so a
// beta-neutral book nets to ~0 (longs and shorts offset).
const sign = (p: PositionRow) => (p.side === "SHORT" ? -1 : 1);
const betaAdj = (p: PositionRow) => sign(p) * p.notional * p.beta;

// A position's prior (yesterday's) gross market value — the denominator for the day's return.
// Null when the name isn't priced (no prev close) so we render "—" rather than a bogus %.
const priorMV = (p: PositionRow) =>
  p.prev_close != null && p.quantity ? Math.abs(p.prev_close * p.quantity) : null;

// Day return = day P&L ÷ prior market value, signed like the P&L columns (a long up 2% and a
// short on a name down 2% both read +2%). Null when prior value is unknown/zero.
const dayPct = (p: PositionRow) => {
  const base = priorMV(p);
  return base ? p.pnl.day / base : null;
};

interface Totals {
  notional: number; // gross notional in the section
  signedNotional: number; // longs − shorts
  betaAdj: number; // signed Σ sign·notional·beta
  betaNotional: number; // Σ notional·beta (name beta) — for the weighted-average beta
  day: number;
  priorMV: number; // Σ prior market value of priced names — denominator for the section day %
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
      priorMV: a.priorMV + (priorMV(p) ?? 0),
      unrealized: a.unrealized + p.pnl.unrealized,
      realized: a.realized + p.pnl.realized,
      total: a.total + p.pnl.total,
    }),
    { notional: 0, signedNotional: 0, betaAdj: 0, betaNotional: 0, day: 0, priorMV: 0, unrealized: 0, realized: 0, total: 0 },
  );
}

// Section day return = Σ day P&L ÷ Σ prior market value (priced names only).
const dayPctOf = (t: Totals) => (t.priorMV ? t.day / t.priorMV : null);

// One shared column layout so the Net, Longs, and Shorts tables line up column-for-column.
function Cols() {
  const widths = ["16%", "7%", "7%", "10%", "12%", "9%", "7%", "9%", "7%", "9%", "7%"];
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
        <th className="pb-2 text-right font-medium">Day %</th>
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
          ? "border-t border-ocean-border/70 font-medium text-ocean-muted"
          : "border-t-2 border-ocean-border font-medium"
      }
    >
      <td className="py-2 text-xs uppercase text-ocean-muted">{label}</td>
      <td></td>
      <td></td>
      <td className="py-2 text-right font-mono">{label.startsWith("Net") ? signedMoney(notional) : money(notional)}</td>
      <td className="py-2 text-right font-mono">{signedMoney(ba)}</td>
      <td className={`py-2 text-right font-mono ${pnlColor(t.day)}`}>{signedMoney(t.day)}</td>
      <td className={`py-2 text-right font-mono ${pnlColor(dayPctOf(t) ?? 0)}`}>{signedPct(dayPctOf(t))}</td>
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
    return <span className="ml-2 text-[10px] uppercase text-ocean-muted">ovr</span>;
  if (p.beta_method === "insufficient_data")
    return <span className="ml-2 text-[10px] uppercase text-status-watch">thin</span>;
  return null;
}

// SWAP-held positions get a small tag; the common CASH case shows nothing (no clutter).
function instrumentFlag(p: PositionRow) {
  if (p.instrument === "SWAP")
    return <span className="ml-2 text-[10px] uppercase text-ocean-accent">swap</span>;
  return null;
}

/** One position row, shared by Longs and both Shorts subgroups so every table lines up. */
function PositionTr({ p }: { p: PositionRow }) {
  return (
    <tr className="border-t border-ocean-border/70">
      <td className="truncate py-2 font-mono">
        {p.ticker}
        {betaFlag(p)}
        {instrumentFlag(p)}
      </td>
      <td className="py-2 text-right font-mono">{p.price == null ? "—" : price(p.price)}</td>
      <td className="py-2 text-right font-mono">
        {p.quantity ? p.quantity.toLocaleString(undefined, { maximumFractionDigits: 0 }) : "—"}
      </td>
      <td className="py-2 text-right font-mono">{money(p.notional)}</td>
      <td className="py-2 text-right font-mono">{signedMoney(betaAdj(p))}</td>
      <td className={`py-2 text-right font-mono ${pnlColor(p.pnl.day)}`}>{signedMoney(p.pnl.day)}</td>
      <td className={`py-2 text-right font-mono ${pnlColor(dayPct(p) ?? 0)}`}>{signedPct(dayPct(p))}</td>
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
      <td colSpan={11} className="pt-4 pb-1 text-xs font-medium uppercase tracking-wide text-ocean-muted">
        {label} <span className="text-ocean-faint">({count})</span>
      </td>
    </tr>
  );
}

const wtdBetaOf = (t: Totals) => (t.notional ? t.betaNotional / t.notional : NaN);

/** Portfolio view: a Net Position summary on top, then Longs and Shorts — all sharing one column
 *  layout so they read top-to-bottom. The Shorts table is split into Systematic and (if any)
 *  Discretionary subgroups, each with its own subtotal, then the grand Total Short — so the two
 *  books stay distinct (I-03) without per-row tags. Net beta-adj is ~0 when hedged to zero beta. */
export function Portfolio({ positions }: { positions: PositionRow[] }) {
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
          Longs <span className="text-ocean-faint">({longs.length})</span>
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
          Shorts <span className="text-ocean-faint">({shorts.length})</span>
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

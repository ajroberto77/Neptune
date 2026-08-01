import { useEffect, useMemo, useState } from "react";
import type { PendingHedge, TradeAction, TransactionInput, TransactionRow } from "../types";
import { money, pnlColor, signedMoney } from "../format";
import { fetchTransactions } from "../api/client";

const ACTIONS: { value: TradeAction; label: string }[] = [
  { value: "BUY", label: "Buy" },
  { value: "SELL", label: "Sell" },
];

const today = () => new Date().toISOString().slice(0, 10);

interface Row {
  key: string;
  portfolioId: string;
  ticker: string;
  action: TradeAction;
  quantity: number;
  price: number;
  fees: number;
  trade_date: string;
  systematic?: boolean; // a row inserted from an approved hedge — books as a systematic short
  error?: string;
}

// Fees are PER SHARE, so the all-in cost = quantity × (execution price + fee per share).
const totalCost = (r: { quantity: number; price: number; fees: number }) =>
  r.quantity * (r.price + r.fees);

let _seq = 0;
const newKey = () => `r${_seq++}`;

/** Trade tab: a single grid of trade rows. Add as many rows as you like; each row picks the
 *  Portfolio it allocates into, or use "Allocate all to" to send every row to one Portfolio.
 *  Submit all books them in one pass — successful rows clear, failures stay flagged. Closing
 *  positions and systematic-short hedges live on the Portfolio and Hedge tabs respectively. */
export function Trade({
  portfolios,
  defaultPortfolioId,
  onSubmit,
  onSubmitHedge,
  onAfterBatch,
  busy,
  pendingHedge = null,
  onConsumeHedge = () => {},
}: {
  portfolios: { id: string; name: string }[];
  defaultPortfolioId: string;
  onSubmit: (portfolioId: string, t: TransactionInput, systematic?: boolean) => Promise<void>;
  onSubmitHedge?: (
    portfolioId: string,
    legs: { ticker: string; action: TradeAction; shares: number; price: number }[],
  ) => Promise<void>;
  onAfterBatch: () => Promise<void>;
  busy: boolean;
  pendingHedge?: PendingHedge | null;
  onConsumeHedge?: () => void;
}) {
  const realDefault = useMemo(
    () => (portfolios.some((p) => p.id === defaultPortfolioId) ? defaultPortfolioId : ""),
    [portfolios, defaultPortfolioId],
  );
  // "" = each row chooses its own Portfolio; otherwise every row allocates to this one.
  const [allocateAll, setAllocateAll] = useState<string>(realDefault);

  const firstPortfolio = portfolios[0]?.id ?? "";
  const blankRow = (): Row => ({
    key: newKey(),
    portfolioId: allocateAll || realDefault || firstPortfolio,
    ticker: "",
    action: "BUY",
    quantity: 0,
    price: 0,
    fees: 0,
    trade_date: today(),
  });

  const [rows, setRows] = useState<Row[]>([blankRow()]);
  const [submitting, setSubmitting] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [blotterKey, setBlotterKey] = useState(0); // bump to refetch the blotter after a batch

  // An approved hedge inserts its reconciliation legs as systematic rows at the TOP of this grid:
  // BUY = buy-to-cover a name being dropped/reduced, SELL = open/increase one. Per-row action is
  // preserved (so you SEE the covers), and any PRIOR systematic rows are replaced — re-accepting a
  // proposal swaps the preview rather than stacking a second copy.
  useEffect(() => {
    if (!pendingHedge) return;
    const hedgeRows: Row[] = pendingHedge.shorts.map((s) => ({
      key: newKey(),
      portfolioId: pendingHedge.portfolioId,
      ticker: s.ticker,
      action: s.action,
      quantity: s.shares,
      price: s.price,
      fees: 0,
      trade_date: today(),
      systematic: true,
    }));
    setRows((rs) => {
      // Drop prior hedge-preview rows (fixes the duplicate-on-re-accept bug); keep manual rows.
      const kept = rs.filter((r) => !r.systematic && (r.ticker.trim() || r.quantity || r.price));
      return [...hedgeRows, ...kept];
    });
    onConsumeHedge();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingHedge]);

  function update(key: string, patch: Partial<Row>) {
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch, error: undefined } : r)));
  }
  function addRow() {
    setRows((rs) => [...rs, blankRow()]);
  }
  function removeRow(key: string) {
    setRows((rs) => (rs.length > 1 ? rs.filter((r) => r.key !== key) : rs.map(() => blankRow())));
  }

  function validate(r: Row): string | null {
    const target = allocateAll || r.portfolioId;
    if (!target) return "no portfolio";
    if (!r.ticker.trim()) return "no ticker";
    if (r.quantity <= 0) return "quantity must be > 0";
    if (r.price <= 0) return "price must be > 0";
    return null;
  }

  // Submit every row; successes clear, invalid/failed rows stay flagged with the reason.
  // Systematic hedge rows are booked as ONE atomic basket per target portfolio (a single
  // replace-and-book call), so re-approving never stacks a second hedge. Manual rows book
  // individually.
  async function submitAll() {
    setSubmitting(true);
    setMsg(null);
    const remaining: Row[] = [];
    let ok = 0;

    const valid: Row[] = [];
    for (const r of rows) {
      const bad = validate(r);
      if (bad) remaining.push({ ...r, error: bad });
      else valid.push(r);
    }

    // Systematic rows → grouped by target portfolio → one atomic hedge-basket call each.
    const sysGroups = new Map<string, Row[]>();
    for (const r of valid.filter((r) => r.systematic)) {
      const target = allocateAll || r.portfolioId;
      (sysGroups.get(target) ?? sysGroups.set(target, []).get(target)!).push(r);
    }
    for (const [target, rs] of sysGroups) {
      try {
        if (!onSubmitHedge) throw new Error("hedge booking unavailable");
        await onSubmitHedge(
          target,
          rs.map((r) => ({
            ticker: r.ticker.trim().toUpperCase(),
            action: r.action,
            shares: r.quantity,
            price: r.price,
          })),
        );
        ok += rs.length;
      } catch (e) {
        for (const r of rs) remaining.push({ ...r, error: String(e) });
      }
    }

    // Manual rows → individual Buy/Sell.
    for (const r of valid.filter((r) => !r.systematic)) {
      const target = allocateAll || r.portfolioId;
      try {
        await onSubmit(target, {
          ticker: r.ticker.trim().toUpperCase(),
          action: r.action,
          quantity: r.quantity,
          price: r.price,
          fee_per_share: r.fees,
          trade_date: r.trade_date,
        }, false);
        ok += 1;
      } catch (e) {
        remaining.push({ ...r, error: String(e) });
      }
    }

    setRows(remaining.length ? remaining : [blankRow()]);
    await onAfterBatch();
    setBlotterKey((k) => k + 1); // refresh the blotter with the just-booked trades
    setSubmitting(false);
    setMsg(
      remaining.length === 0
        ? `Submitted ${ok} trade${ok === 1 ? "" : "s"}.`
        : `Submitted ${ok}; ${remaining.length} need attention — see flags.`,
    );
  }

  const disabled = busy || submitting;

  if (portfolios.length === 0) {
    return (
      <p className="text-sm text-ocean-muted">
        No portfolios yet. Create a portfolio before trading into it.
      </p>
    );
  }

  const systematicCount = rows.filter((r) => r.systematic).length;

  return (
    <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
          Trades <span className="text-ocean-faint">({rows.length})</span>
          {systematicCount > 0 && (
            <span className="ml-2 text-xs text-status-ok">
              · {systematicCount} systematic hedge {systematicCount === 1 ? "row" : "rows"}
            </span>
          )}
        </h3>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-ocean-muted">
            Allocate all to
            <select
              className="np-input py-1"
              value={allocateAll}
              onChange={(e) => setAllocateAll(e.target.value)}
            >
              <option value="">Per row</option>
              {portfolios.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={disabled}
            onClick={submitAll}
            className="rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-ocean-accent/80 disabled:opacity-50"
          >
            {submitting ? "Submitting…" : "Submit all"}
          </button>
        </div>
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase text-ocean-muted">
            <th className="pb-2 font-medium">Ticker</th>
            <th className="pb-2 font-medium">Action</th>
            <th className="pb-2 font-medium">Quantity</th>
            <th className="pb-2 font-medium">Avg Price</th>
            <th className="pb-2 font-medium">Txn Fee/sh</th>
            <th className="pb-2 text-right font-medium">Total Cost</th>
            <th className="pb-2 font-medium">Trade Date</th>
            <th className="pb-2 font-medium">Portfolio</th>
            <th className="pb-2 font-medium"></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.key}
              className={`border-t border-ocean-border/70 ${r.error ? "bg-status-breach/10" : r.systematic ? "bg-status-ok/5" : ""}`}
            >
              <td className="py-2 pr-2">
                <div className="flex items-center gap-1">
                  <input
                    className="np-input w-24 font-mono"
                    aria-label="Ticker"
                    value={r.ticker}
                    onChange={(e) => update(r.key, { ticker: e.target.value })}
                  />
                  {r.systematic && (
                    <span className="rounded bg-status-ok/20 px-1 py-0.5 text-[9px] uppercase text-status-ok">
                      sys
                    </span>
                  )}
                </div>
              </td>
              <td className="py-2 pr-2">
                <select
                  className="np-input py-1"
                  aria-label="Action"
                  value={r.action}
                  onChange={(e) => update(r.key, { action: e.target.value as TradeAction })}
                >
                  {ACTIONS.map((a) => (
                    <option key={a.value} value={a.value}>
                      {a.label}
                    </option>
                  ))}
                </select>
              </td>
              <td className="py-2 pr-2">
                <input
                  className="np-input w-20"
                  type="number"
                  step="any"
                  aria-label="Quantity"
                  value={r.quantity || ""}
                  onChange={(e) => update(r.key, { quantity: Number(e.target.value) })}
                />
              </td>
              <td className="py-2 pr-2">
                <input
                  className="np-input w-24"
                  type="number"
                  step="any"
                  aria-label="Average Price"
                  value={r.price || ""}
                  onChange={(e) => update(r.key, { price: Number(e.target.value) })}
                />
              </td>
              <td className="py-2 pr-2">
                <input
                  className="np-input w-20"
                  type="number"
                  step="any"
                  aria-label="Transaction Fees"
                  value={r.fees || ""}
                  onChange={(e) => update(r.key, { fees: Number(e.target.value) })}
                />
              </td>
              <td className="py-2 pr-2 text-right font-mono" aria-label="Total Cost">
                {money(totalCost(r))}
              </td>
              <td className="py-2 pr-2">
                <input
                  className="np-input"
                  type="date"
                  aria-label="Trade Date"
                  value={r.trade_date}
                  onChange={(e) => update(r.key, { trade_date: e.target.value })}
                />
              </td>
              <td className="py-2 pr-2">
                <select
                  className="np-input py-1"
                  aria-label="Portfolio"
                  value={allocateAll || r.portfolioId}
                  disabled={!!allocateAll}
                  onChange={(e) => update(r.key, { portfolioId: e.target.value })}
                >
                  {portfolios.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </td>
              <td className="py-2 text-right">
                {r.error && (
                  <span className="mr-2 text-xs text-status-breach" title={r.error}>
                    {r.error}
                  </span>
                )}
                <button
                  disabled={disabled}
                  onClick={() => removeRow(r.key)}
                  className="rounded border border-ocean-border px-2 py-1 text-xs text-ocean-muted hover:text-slate-200 disabled:opacity-40"
                >
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="mt-3 flex items-center gap-3">
        <button
          disabled={disabled}
          onClick={addRow}
          className="rounded border border-ocean-border px-3 py-1.5 text-sm text-slate-200 hover:bg-ocean-border/40 disabled:opacity-40"
        >
          + Add row
        </button>
        {msg && <span className="text-sm text-ocean-muted">{msg}</span>}
      </div>
      <p className="mt-3 text-xs text-ocean-muted">
        Buy/Sell nets against the current holding. Rows from an approved hedge book as systematic
        shorts (kept distinct from discretionary — I-03); Neptune never routes orders (I-01).
      </p>

      <Blotter portfolioId={allocateAll || realDefault || firstPortfolio} reloadKey={blotterKey} />
    </div>
  );
}

/** Executed-trade ledger for the active book: desk Buy/Sells and the buy-to-cover / sell-short
 *  legs booked when a hedge is approved. Read-only history — Neptune records, never routes. */
function Blotter({ portfolioId, reloadKey }: { portfolioId: string; reloadKey: number }) {
  const [rows, setRows] = useState<TransactionRow[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!portfolioId) return;
    fetchTransactions(portfolioId, 100)
      .then(setRows)
      .catch((e) => setErr(String(e)));
  }, [portfolioId, reloadKey]);

  return (
    <div className="mt-8">
      <h3 className="mb-3 font-display text-sm uppercase tracking-wide text-ocean-muted">
        Blotter <span className="text-ocean-faint">(executed trades)</span>
      </h3>
      {err && <p className="text-sm text-status-breach">{err}</p>}
      {rows.length === 0 ? (
        <p className="text-sm text-ocean-muted">No trades booked yet.</p>
      ) : (
        <div className="max-h-96 overflow-auto rounded-lg border border-ocean-border">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 bg-ocean-bg text-xs uppercase text-ocean-muted">
              <tr>
                <th className="px-3 py-2">Date</th>
                <th className="px-3 py-2">Ticker</th>
                <th className="px-3 py-2">Action</th>
                <th className="px-3 py-2 text-right">Shares</th>
                <th className="px-3 py-2 text-right">Price</th>
                <th className="px-3 py-2">Effect</th>
                <th className="px-3 py-2 text-right">Realized P&L</th>
                <th className="px-3 py-2">Origin</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.id} className="border-t border-ocean-border/70">
                  <td className="px-3 py-1.5 font-mono text-ocean-muted">{t.trade_date}</td>
                  <td className="px-3 py-1.5 font-mono">{t.ticker}</td>
                  <td
                    className={`px-3 py-1.5 font-medium ${
                      t.action === "BUY" ? "text-status-ok" : "text-status-breach"
                    }`}
                  >
                    {t.action}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono">
                    {t.quantity.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono">{money(t.price)}</td>
                  <td className="px-3 py-1.5 text-ocean-muted">{t.effect}</td>
                  <td className={`px-3 py-1.5 text-right font-mono ${pnlColor(t.realized_pnl)}`}>
                    {t.realized_pnl ? signedMoney(t.realized_pnl) : "—"}
                  </td>
                  <td className="px-3 py-1.5">
                    <span
                      className={`rounded px-2 py-0.5 text-xs ${
                        t.origin === "HEDGE"
                          ? "bg-ocean-accent/20 text-ocean-accent"
                          : "bg-ocean-border/40 text-ocean-muted"
                      }`}
                    >
                      {t.origin === "HEDGE" ? "Hedge" : "Manual"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

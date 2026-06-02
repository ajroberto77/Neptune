import { useMemo, useState } from "react";
import type { PendingHedge, TradeAction, TransactionInput } from "../types";
import { money, price } from "../format";

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
  onAfterBatch,
  busy,
  pendingHedge = null,
  onBookHedge = () => {},
  onDiscardHedge = () => {},
  bookingHedge = false,
}: {
  portfolios: { id: string; name: string }[];
  defaultPortfolioId: string;
  onSubmit: (portfolioId: string, t: TransactionInput) => Promise<void>;
  onAfterBatch: () => Promise<void>;
  busy: boolean;
  pendingHedge?: PendingHedge | null;
  onBookHedge?: () => void;
  onDiscardHedge?: () => void;
  bookingHedge?: boolean;
}) {
  const hedgeBook = portfolios.find((p) => p.id === pendingHedge?.portfolioId);
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
  async function submitAll() {
    setSubmitting(true);
    setMsg(null);
    const remaining: Row[] = [];
    let ok = 0;
    for (const r of rows) {
      const bad = validate(r);
      if (bad) {
        remaining.push({ ...r, error: bad });
        continue;
      }
      const target = allocateAll || r.portfolioId;
      try {
        await onSubmit(target, {
          ticker: r.ticker.trim().toUpperCase(),
          action: r.action,
          quantity: r.quantity,
          price: r.price,
          fee_per_share: r.fees,
          trade_date: r.trade_date,
        });
        ok += 1;
      } catch (e) {
        remaining.push({ ...r, error: String(e) });
      }
    }
    setRows(remaining.length ? remaining : [blankRow()]);
    await onAfterBatch();
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

  return (
    <div className="space-y-6">
      {pendingHedge && (
        <div className="rounded-lg border border-status-ok/40 bg-ocean-panel p-5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <h3 className="font-display text-sm uppercase tracking-wide text-status-ok">
              Systematic Hedge — approved, pending booking
              <span className="ml-2 text-ocean-muted/60">
                ({pendingHedge.shorts.length} shorts → {hedgeBook?.name ?? pendingHedge.portfolioId})
              </span>
            </h3>
            <div className="flex gap-2">
              <button
                onClick={onBookHedge}
                disabled={bookingHedge}
                className="rounded bg-status-ok/80 px-3 py-1.5 text-sm font-medium text-white hover:bg-status-ok disabled:opacity-50"
              >
                {bookingHedge ? "Booking…" : "Book systematic hedge"}
              </button>
              <button
                onClick={onDiscardHedge}
                disabled={bookingHedge}
                className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200 disabled:opacity-50"
              >
                Discard
              </button>
            </div>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-ocean-muted">
                <th className="pb-2 font-medium">Ticker</th>
                <th className="pb-2 font-medium">Sector</th>
                <th className="pb-2 text-right font-medium">Shares</th>
                <th className="pb-2 text-right font-medium">Price</th>
                <th className="pb-2 text-right font-medium">Short Notional</th>
                <th className="pb-2 text-right font-medium">Beta</th>
              </tr>
            </thead>
            <tbody>
              {pendingHedge.shorts.map((s) => (
                <tr key={s.ticker} className="border-t border-ocean-border/60">
                  <td className="py-2 font-mono">{s.ticker}</td>
                  <td className="py-2 text-ocean-muted">{s.sector ?? "—"}</td>
                  <td className="py-2 text-right font-mono">
                    {s.shares.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </td>
                  <td className="py-2 text-right font-mono">{price(s.price)}</td>
                  <td className="py-2 text-right font-mono">{money(s.notional)}</td>
                  <td className="py-2 text-right font-mono">{s.beta.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-3 text-xs text-ocean-muted">
            Booking records these as systematic shorts (kept distinct from manual/discretionary
            trades — I-03) and never routes an order (I-01).
          </p>
        </div>
      )}

    <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
          Trades <span className="text-ocean-muted/60">({rows.length})</span>
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
            <tr key={r.key} className={`border-t border-ocean-border/60 ${r.error ? "bg-status-breach/10" : ""}`}>
              <td className="py-2 pr-2">
                <input
                  className="np-input w-24 font-mono"
                  aria-label="Ticker"
                  value={r.ticker}
                  onChange={(e) => update(r.key, { ticker: e.target.value })}
                />
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
      <p className="mt-3 text-xs text-ocean-muted/70">
        Buy/Sell nets against the current holding. Closing positions is on the Portfolio tab;
        systematic-short hedges are booked from the Hedge tab.
      </p>
    </div>
    </div>
  );
}

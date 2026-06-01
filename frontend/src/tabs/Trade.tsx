import { useState } from "react";
import type { PositionRow, TradeAction, TransactionInput } from "../types";
import { money, price } from "../format";

const ACTIONS: { value: TradeAction; label: string }[] = [
  { value: "BUY", label: "Buy" },
  { value: "SELL", label: "Sell" },
];

const today = () => new Date().toISOString().slice(0, 10);

/** Trade tab: book a manual Buy/Sell. Direction (long/short) and whether it opens, adds,
 *  reduces, closes, or covers is DERIVED from the current holding by netting — the desk
 *  picks only Buy or Sell. Systematic-short hedges are booked from Hedge Approval, not here. */
export function Trade({
  positions,
  onRecord,
  onClose,
  busy,
}: {
  positions: PositionRow[];
  onRecord: (t: TransactionInput) => Promise<void>;
  onClose: (positionId: number, quantity: number, exitPrice: number) => Promise<void>;
  busy: boolean;
}) {
  const [form, setForm] = useState<TransactionInput>({
    ticker: "",
    action: "BUY",
    quantity: 0,
    price: 0,
    trade_date: today(),
  });
  const [closing, setClosing] = useState<Record<number, string>>({});
  const [msg, setMsg] = useState<string | null>(null);

  function up(patch: Partial<TransactionInput>) {
    setForm((f) => ({ ...f, ...patch }));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    if (!form.ticker.trim() || form.quantity <= 0 || form.price <= 0) {
      setMsg("Enter a ticker, a positive quantity, and a positive price.");
      return;
    }
    await onRecord({ ...form, ticker: form.ticker.trim().toUpperCase() });
    setMsg(`Recorded ${form.quantity} ${form.ticker.toUpperCase()} @ ${form.price}`);
    setForm({ ...form, ticker: "", quantity: 0, price: 0 });
  }

  const openPositions = positions.filter((p) => p.id !== null && p.quantity > 0);

  return (
    <div className="space-y-6">
      {/* --- Record a transaction --- */}
      <form
        onSubmit={submit}
        className="rounded-lg border border-ocean-border bg-ocean-panel p-5"
      >
        <h3 className="mb-3 font-display text-sm uppercase tracking-wide text-ocean-muted">
          Record a transaction
        </h3>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <label className="block">
            <span className="mb-1 block text-xs text-ocean-muted">Ticker</span>
            <input
              className="np-input font-mono"
              value={form.ticker}
              onChange={(e) => up({ ticker: e.target.value })}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-ocean-muted">Action</span>
            <select
              className="np-input"
              value={form.action}
              onChange={(e) => up({ action: e.target.value as TradeAction })}
            >
              {ACTIONS.map((a) => (
                <option key={a.value} value={a.value}>
                  {a.label}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-ocean-muted">Quantity</span>
            <input
              className="np-input"
              type="number"
              value={form.quantity || ""}
              onChange={(e) => up({ quantity: Number(e.target.value) })}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-ocean-muted">Price</span>
            <input
              className="np-input"
              type="number"
              value={form.price || ""}
              onChange={(e) => up({ price: Number(e.target.value) })}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-ocean-muted">Date</span>
            <input
              className="np-input"
              type="date"
              value={form.trade_date}
              onChange={(e) => up({ trade_date: e.target.value })}
            />
          </label>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <button
            type="submit"
            disabled={busy}
            className="rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-ocean-accent/80 disabled:opacity-50"
          >
            Record transaction
          </button>
          {msg && <span className="text-sm text-ocean-muted">{msg}</span>}
        </div>
        <p className="mt-3 text-xs text-ocean-muted/70">
          Buy/Sell nets against your current holding: a Sell reduces a long, then opens a
          (discretionary) short with any remainder; a Buy covers a short, then opens a long.
          Systematic-short hedges are booked from Hedge Approval, not here.
        </p>
      </form>

      {/* --- Open positions with close action --- */}
      <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
        <h3 className="mb-3 font-display text-sm uppercase tracking-wide text-ocean-muted">
          Open positions <span className="text-ocean-muted/60">({openPositions.length})</span>
        </h3>
        {openPositions.length === 0 ? (
          <p className="text-sm text-ocean-muted">
            No closeable positions yet (positions need recorded lots to close).
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-ocean-muted">
                <th className="pb-2 font-medium">Ticker</th>
                <th className="pb-2 font-medium">Side</th>
                <th className="pb-2 text-right font-medium">Qty</th>
                <th className="pb-2 text-right font-medium">Avg price</th>
                <th className="pb-2 text-right font-medium">Notional</th>
                <th className="pb-2 text-right font-medium">Close @ price</th>
                <th className="pb-2 text-right font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {openPositions.map((p) => (
                <tr key={p.id!} className="border-t border-ocean-border/60">
                  <td className="py-2 font-mono">{p.ticker}</td>
                  <td className="py-2 text-xs text-ocean-muted">
                    {p.side}
                    {p.side === "SHORT" && ` · ${p.short_type.toLowerCase()}`}
                  </td>
                  <td className="py-2 text-right font-mono">{p.quantity}</td>
                  <td className="py-2 text-right font-mono">
                    {p.quantity > 0 ? price(p.notional / p.quantity) : "—"}
                  </td>
                  <td className="py-2 text-right font-mono">{money(p.notional)}</td>
                  <td className="py-2 text-right">
                    <input
                      className="np-input w-28 text-right"
                      type="number"
                      placeholder="exit price"
                      value={closing[p.id!] ?? ""}
                      onChange={(e) =>
                        setClosing((c) => ({ ...c, [p.id!]: e.target.value }))
                      }
                    />
                  </td>
                  <td className="py-2 text-right">
                    <button
                      disabled={busy || !closing[p.id!]}
                      onClick={() => onClose(p.id!, p.quantity, Number(closing[p.id!]))}
                      className="rounded border border-ocean-border px-2 py-1 text-xs text-ocean-muted hover:text-slate-200 disabled:opacity-40"
                    >
                      Close all
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

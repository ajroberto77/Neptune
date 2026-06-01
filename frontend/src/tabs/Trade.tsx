import { useMemo, useState } from "react";
import type { TradeAction, TransactionInput } from "../types";

const ACTIONS: { value: TradeAction; label: string }[] = [
  { value: "BUY", label: "Buy" },
  { value: "SELL", label: "Sell" },
];

const today = () => new Date().toISOString().slice(0, 10);

type Ticket = TransactionInput & { key: string; error?: string };

let _seq = 0;
const newKey = () => `t${_seq++}`;

/** Trade tab: stage a BATCH of Buy/Sell tickets against one chosen book, then submit them
 *  together. Each ticket nets against the current holding (a Sell reduces a long then opens a
 *  discretionary short; a Buy covers a short then opens a long). Open positions live on the
 *  Portfolio tab — closing is done there, not here. Systematic-short hedges come from Hedge
 *  Approval. You trade into a real book, never the Consolidated roll-up. */
export function Trade({
  portfolios,
  defaultPortfolioId,
  onSubmit,
  onAfterBatch,
  busy,
}: {
  portfolios: { id: string; name: string }[];
  defaultPortfolioId: string;
  onSubmit: (portfolioId: string, t: TransactionInput) => Promise<void>;
  onAfterBatch: () => Promise<void>;
  busy: boolean;
}) {
  // Trade into a real book. If the app is on Consolidated, fall back to the first real book.
  const initialTarget = useMemo(() => {
    if (portfolios.some((p) => p.id === defaultPortfolioId)) return defaultPortfolioId;
    return portfolios[0]?.id ?? "";
  }, [portfolios, defaultPortfolioId]);
  const [target, setTarget] = useState(initialTarget);

  const [draft, setDraft] = useState<TransactionInput>({
    ticker: "",
    action: "BUY",
    quantity: 0,
    price: 0,
    trade_date: today(),
  });
  const [batch, setBatch] = useState<Ticket[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  function up(patch: Partial<TransactionInput>) {
    setDraft((d) => ({ ...d, ...patch }));
  }

  function addTicket(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    if (!draft.ticker.trim() || draft.quantity <= 0 || draft.price <= 0) {
      setMsg("Enter a ticker, a positive quantity, and a positive price.");
      return;
    }
    setBatch((b) => [
      ...b,
      { ...draft, ticker: draft.ticker.trim().toUpperCase(), key: newKey() },
    ]);
    setDraft({ ...draft, ticker: "", quantity: 0, price: 0 });
  }

  function removeTicket(key: string) {
    setBatch((b) => b.filter((t) => t.key !== key));
  }

  // Submit every ticket; succeeded ones drop off, failures stay flagged with their error.
  async function submitAll() {
    if (!target || batch.length === 0) return;
    setSubmitting(true);
    setMsg(null);
    const remaining: Ticket[] = [];
    let ok = 0;
    for (const t of batch) {
      try {
        await onSubmit(target, t);
        ok += 1;
      } catch (e) {
        remaining.push({ ...t, error: String(e) });
      }
    }
    setBatch(remaining);
    await onAfterBatch();
    setSubmitting(false);
    setMsg(
      remaining.length === 0
        ? `Submitted ${ok} ticket${ok === 1 ? "" : "s"}.`
        : `Submitted ${ok}; ${remaining.length} failed — see flags below.`,
    );
  }

  const disabled = busy || submitting;

  if (portfolios.length === 0) {
    return (
      <p className="text-sm text-ocean-muted">
        No portfolios yet. Create a book before trading into it.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      {/* --- Stage a ticket --- */}
      <form onSubmit={addTicket} className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
            New trade
          </h3>
          <label className="flex items-center gap-2 text-xs text-ocean-muted">
            Into book
            <select className="np-input py-1" value={target} onChange={(e) => setTarget(e.target.value)}>
              {portfolios.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <label className="block">
            <span className="mb-1 block text-xs text-ocean-muted">Ticker</span>
            <input
              className="np-input font-mono"
              value={draft.ticker}
              onChange={(e) => up({ ticker: e.target.value })}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-ocean-muted">Action</span>
            <select
              className="np-input"
              value={draft.action}
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
              value={draft.quantity || ""}
              onChange={(e) => up({ quantity: Number(e.target.value) })}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-ocean-muted">Price</span>
            <input
              className="np-input"
              type="number"
              value={draft.price || ""}
              onChange={(e) => up({ price: Number(e.target.value) })}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-ocean-muted">Date</span>
            <input
              className="np-input"
              type="date"
              value={draft.trade_date}
              onChange={(e) => up({ trade_date: e.target.value })}
            />
          </label>
          <div className="flex items-end">
            <button
              type="submit"
              className="rounded border border-ocean-border px-3 py-1.5 text-sm text-slate-200 hover:bg-ocean-border/40"
            >
              Add to batch
            </button>
          </div>
        </div>
        {msg && <p className="mt-3 text-sm text-ocean-muted">{msg}</p>}
        <p className="mt-3 text-xs text-ocean-muted/70">
          Buy/Sell nets against the current holding. Closing positions is done on the Portfolio
          tab. Systematic-short hedges are booked from Hedge Approval, not here.
        </p>
      </form>

      {/* --- Pending batch --- */}
      <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
            Pending batch <span className="text-ocean-muted/60">({batch.length})</span>
          </h3>
          <button
            disabled={disabled || batch.length === 0}
            onClick={submitAll}
            className="rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-ocean-accent/80 disabled:opacity-50"
          >
            {submitting ? "Submitting…" : "Submit all"}
          </button>
        </div>
        {batch.length === 0 ? (
          <p className="text-sm text-ocean-muted">No staged trades. Add tickets above.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-ocean-muted">
                <th className="pb-2 font-medium">Ticker</th>
                <th className="pb-2 font-medium">Action</th>
                <th className="pb-2 text-right font-medium">Qty</th>
                <th className="pb-2 text-right font-medium">Price</th>
                <th className="pb-2 font-medium">Date</th>
                <th className="pb-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {batch.map((t) => (
                <tr
                  key={t.key}
                  className={`border-t border-ocean-border/60 ${t.error ? "bg-status-breach/10" : ""}`}
                >
                  <td className="py-2 font-mono">{t.ticker}</td>
                  <td className="py-2">{t.action}</td>
                  <td className="py-2 text-right font-mono">{t.quantity}</td>
                  <td className="py-2 text-right font-mono">{t.price}</td>
                  <td className="py-2 text-xs text-ocean-muted">{t.trade_date}</td>
                  <td className="py-2 text-right">
                    {t.error && (
                      <span className="mr-2 text-xs text-status-breach" title={t.error}>
                        failed
                      </span>
                    )}
                    <button
                      disabled={disabled}
                      onClick={() => removeTicket(t.key)}
                      className="rounded border border-ocean-border px-2 py-1 text-xs text-ocean-muted hover:text-slate-200 disabled:opacity-40"
                    >
                      Remove
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

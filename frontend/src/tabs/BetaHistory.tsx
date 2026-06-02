import { useEffect, useState } from "react";
import type { BetaHistory, BetaStats, HedgeBacktest, HedgeCalibration } from "../types";
import { fetchBetaHistory, fetchHedgeBacktest, fetchHedgeCalibration } from "../api/client";

/** A tiny inline sparkline (no chart dependency). Maps values to a fixed viewBox; an optional
 *  baseline (e.g. β = 0) is drawn so you can see sign and drift at a glance. */
function Sparkline({
  values,
  width = 160,
  height = 32,
  baseline,
}: {
  values: number[];
  width?: number;
  height?: number;
  baseline?: number;
}) {
  if (values.length < 2) return <span className="text-ocean-muted/50">—</span>;
  const lo = Math.min(...values, baseline ?? Infinity);
  const hi = Math.max(...values, baseline ?? -Infinity);
  const span = hi - lo || 1;
  const x = (i: number) => (i / (values.length - 1)) * (width - 2) + 1;
  const y = (v: number) => height - 1 - ((v - lo) / span) * (height - 2);
  const path = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return (
    <svg width={width} height={height} className="overflow-visible">
      {baseline !== undefined && lo <= baseline && baseline <= hi && (
        <line x1={1} x2={width - 1} y1={y(baseline)} y2={y(baseline)}
              stroke="currentColor" strokeWidth={0.5} className="text-ocean-border" />
      )}
      <path d={path} fill="none" stroke="currentColor" strokeWidth={1.5}
            className="text-ocean-accent" />
    </svg>
  );
}

function StatCells({ stats }: { stats: BetaStats | null }) {
  if (!stats) return <>
    <td className="py-2 text-right font-mono text-ocean-muted/50">—</td>
    <td className="py-2 text-right font-mono text-ocean-muted/50">—</td>
    <td className="py-2 text-right font-mono text-ocean-muted/50">—</td>
    <td className="py-2 text-right font-mono text-ocean-muted/50">—</td>
  </>;
  // Flag a name whose beta has drifted a lot over the window (range > 0.5).
  const drifty = stats.range > 0.5;
  return (
    <>
      <td className="py-2 text-right font-mono">{stats.last.toFixed(2)}</td>
      <td className="py-2 text-right font-mono">{stats.mean.toFixed(2)}</td>
      <td className="py-2 text-right font-mono">{stats.std.toFixed(2)}</td>
      <td className={`py-2 text-right font-mono ${drifty ? "text-status-watch" : ""}`}>
        {stats.min.toFixed(2)}–{stats.max.toFixed(2)}
      </td>
    </>
  );
}

/** Beta tab: how consistent the book's betas are over time. The net line is the book's net beta
 *  on a trailing-window walk-forward (current weights held fixed), so it isolates beta DRIFT from
 *  position changes. The table shows each holding's current/mean/std beta and its min–max range
 *  (a wide range = an unstable beta the hedge keeps re-chasing). */
export function BetaHistory({ portfolioId }: { portfolioId: string }) {
  const [data, setData] = useState<BetaHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [weeks, setWeeks] = useState(26);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchBetaHistory(portfolioId, weeks, 5)
      .then(setData)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [portfolioId, weeks]);

  const netValues = (data?.net ?? []).map((p) => p.net_beta);
  const ns = data?.net_stats;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-ocean-muted">
          Point-in-time betas on a 252-day trailing window, current weights held fixed — shows how
          stable the betas (and the hedge target) are over time.
        </p>
        <label className="flex items-center gap-2 text-xs text-ocean-muted">
          Window
          <select className="np-input py-1" value={weeks}
                  onChange={(e) => setWeeks(Number(e.target.value))}>
            <option value={13}>3 months</option>
            <option value={26}>6 months</option>
            <option value={52}>1 year</option>
          </select>
        </label>
      </div>

      {error && (
        <div className="rounded border border-status-breach/40 bg-status-breach/10 p-3 text-sm text-status-breach">
          {error}
        </div>
      )}
      {loading && <p className="text-sm text-ocean-muted">Loading beta history…</p>}

      {data && (
        <>
          <div className="rounded-lg border border-ocean-accent/40 bg-ocean-panel p-5">
            <div className="flex items-center justify-between">
              <h3 className="font-display text-sm uppercase tracking-wide text-ocean-accent">
                Net Book Beta over time
              </h3>
              <Sparkline values={netValues} width={320} height={48} baseline={0} />
            </div>
            {ns && (
              <div className="mt-3 flex flex-wrap gap-6 text-sm">
                <Stat label="Now" value={ns.last.toFixed(3)} />
                <Stat label="Mean" value={ns.mean.toFixed(3)} />
                <Stat label="Std" value={ns.std.toFixed(3)} />
                <Stat label="Range" value={`${ns.min.toFixed(3)} … ${ns.max.toFixed(3)}`} />
              </div>
            )}
            {netValues.length < 2 && (
              <p className="mt-2 text-sm text-ocean-muted">
                Not enough priced history to chart the net beta.
              </p>
            )}
          </div>

          <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
            <h3 className="mb-3 font-display text-sm uppercase tracking-wide text-ocean-muted">
              Per-position beta consistency
            </h3>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-ocean-muted">
                  <th className="pb-2 font-medium">Ticker</th>
                  <th className="pb-2 font-medium">Side</th>
                  <th className="pb-2 text-right font-medium">Beta now</th>
                  <th className="pb-2 text-right font-medium">Mean</th>
                  <th className="pb-2 text-right font-medium">Std</th>
                  <th className="pb-2 text-right font-medium">Min–Max</th>
                  <th className="pb-2 text-right font-medium">History</th>
                </tr>
              </thead>
              <tbody>
                {data.positions.map((p) => (
                  <tr key={`${p.ticker}-${p.side}-${p.short_type}`}
                      className="border-t border-ocean-border/60">
                    <td className="py-2 font-mono">{p.ticker}</td>
                    <td className="py-2 text-xs uppercase text-ocean-muted">
                      {p.side === "SHORT"
                        ? p.short_type === "SYSTEMATIC" ? "short·sys" : "short·disc"
                        : "long"}
                    </td>
                    <StatCells stats={p.stats} />
                    <td className="py-2 text-right">
                      <Sparkline values={p.series.map((s) => s.beta)} baseline={0} />
                    </td>
                  </tr>
                ))}
                {data.positions.length === 0 && (
                  <tr>
                    <td colSpan={7} className="py-2 text-sm text-ocean-muted">No positions.</td>
                  </tr>
                )}
              </tbody>
            </table>
            <p className="mt-3 text-xs text-ocean-muted">
              A wide Min–Max range (highlighted) means the name's beta keeps moving — the hedge will
              re-size it each run. Net line at β = 0 is the baseline; the gray line marks zero.
            </p>
          </div>
        </>
      )}

      <HedgeBacktestPanel portfolioId={portfolioId} />
    </div>
  );
}

/** Walk-forward HEDGE backtest: re-optimizes the hedge at each past rebalance and plots the
 *  REALIZED net beta vs the ±tol band — the control metric. The calibrate sweep shows how the
 *  beta-add budget trades realized control against turnover, so the level is chosen, not guessed. */
function HedgeBacktestPanel({ portfolioId }: { portfolioId: string }) {
  const [bt, setBt] = useState<HedgeBacktest | null>(null);
  const [cal, setCal] = useState<HedgeCalibration | null>(null);
  const [busy, setBusy] = useState<null | "bt" | "cal">(null);
  const [err, setErr] = useState<string | null>(null);

  async function run(kind: "bt" | "cal") {
    setBusy(kind);
    setErr(null);
    try {
      if (kind === "bt") setBt(await fetchHedgeBacktest(portfolioId));
      else setCal(await fetchHedgeCalibration(portfolioId));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }

  // Recommended budget: the LOOSEST (largest) that still passes the 95% coverage target.
  const recommended = cal?.grid
    .filter((r) => r.coverage >= 0.95)
    .reduce<number | null>((best, r) => (best === null || r.beta_add_budget > best ? r.beta_add_budget : best), null);

  return (
    <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
          Hedge backtest (walk-forward)
        </h3>
        <div className="flex gap-2">
          <button onClick={() => run("bt")} disabled={busy !== null}
                  className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200 disabled:opacity-50">
            {busy === "bt" ? "Replaying…" : "Run backtest"}
          </button>
          <button onClick={() => run("cal")} disabled={busy !== null}
                  className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200 disabled:opacity-50">
            {busy === "cal" ? "Sweeping…" : "Calibrate budget"}
          </button>
        </div>
      </div>
      <p className="mt-1 text-xs text-ocean-muted">
        Re-optimizes the hedge at each past month, holds it forward, and measures the REALIZED net
        beta. Calibration sweeps the beta-add budget; pick the loosest that keeps coverage high.
      </p>

      {err && <div className="mt-3 rounded border border-status-breach/40 bg-status-breach/10 p-3 text-sm text-status-breach">{err}</div>}

      {bt && (
        <div className="mt-4">
          <div className="flex items-center justify-between">
            <div className="flex flex-wrap gap-6 text-sm">
              <Stat label="Realized β RMSE" value={bt.rmse.toFixed(3)} />
              <Stat label={`Coverage (|β|≤${bt.tol})`} value={`${Math.round(bt.coverage * 100)}%`} />
              <Stat label="Mean turnover" value={bt.mean_turnover.toFixed(3)} />
              <Stat label="Rebalances" value={String(bt.n_rebalances)} />
            </div>
            <Sparkline values={bt.points.map((p) => p.realized_net_beta)} width={300} height={48} baseline={0} />
          </div>
          {bt.n_rebalances < 2 && (
            <p className="mt-2 text-sm text-ocean-muted">
              Not enough priced history to replay. Backfill more years (Settings) and retry.
            </p>
          )}
        </div>
      )}

      {cal && (
        <div className="mt-5">
          <h4 className="mb-2 text-xs font-medium uppercase text-ocean-muted">
            Budget calibration — current: {cal.current_budget}
          </h4>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-ocean-muted">
                <th className="pb-2 font-medium">Beta-add budget</th>
                <th className="pb-2 text-right font-medium">Realized β RMSE</th>
                <th className="pb-2 text-right font-medium">Coverage</th>
                <th className="pb-2 text-right font-medium">Mean turnover</th>
              </tr>
            </thead>
            <tbody>
              {cal.grid.map((r) => {
                const isRec = recommended !== null && r.beta_add_budget === recommended;
                return (
                  <tr key={r.beta_add_budget}
                      className={`border-t border-ocean-border/60 ${isRec ? "bg-status-ok/10" : ""}`}>
                    <td className="py-2 font-mono">
                      {r.beta_add_budget}{isRec ? " ◄ rec" : ""}
                    </td>
                    <td className="py-2 text-right font-mono">{r.rmse.toFixed(3)}</td>
                    <td className={`py-2 text-right font-mono ${r.coverage >= 0.95 ? "text-status-ok" : "text-status-watch"}`}>
                      {Math.round(r.coverage * 100)}%
                    </td>
                    <td className="py-2 text-right font-mono">{r.mean_turnover.toFixed(3)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-3 text-xs text-ocean-muted">
            Recommended = the loosest budget still keeping coverage ≥ 95% (looser preserves
            factor-matching room without hurting realized control). Tightening past it only adds
            turnover. This is a suggestion — the hard |β| ≤ {cal.tol} limit is policy, unchanged.
          </p>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase text-ocean-muted">{label}</div>
      <div className="font-mono text-lg">{value}</div>
    </div>
  );
}

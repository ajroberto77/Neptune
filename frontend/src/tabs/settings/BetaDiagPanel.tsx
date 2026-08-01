import type { BetaDiagnostics } from "../../types";
import { Card, CardTitle } from "./primitives";

/** Per-ticker beta diagnostics: the regression inputs behind a surprising beta — stored bars,
 *  date span vs the benchmark, observations used, forward-filled gap days, and raw vs shrunk
 *  beta — so "too low/high" can be traced to data vs a genuine fit. */
export function BetaDiagPanel({ diag }: { diag: BetaDiagnostics }) {
  const b = diag.benchmark;
  return (
    <Card>
      <CardTitle>Beta diagnostics</CardTitle>
      <p className="mt-1 text-xs text-ocean-muted">
        Benchmark {b.ticker}: {b.bars} bars, {b.first_bar} → {b.last_bar} ({b.obs_used} obs used).
        A name with far fewer bars, a later start, or many gap days will read a muted beta — that's
        data, not the market. Names below {diag.min_obs} obs are held at the 1.0 prior.
      </p>
      <table className="mt-3 w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase text-ocean-muted">
            <th className="pb-2 font-medium">Ticker</th>
            <th className="pb-2 text-right font-medium">Bars</th>
            <th className="pb-2 font-medium">Span</th>
            <th className="pb-2 text-right font-medium">Obs used</th>
            <th className="pb-2 text-right font-medium">Gap days</th>
            <th className="pb-2 text-right font-medium">Raw β</th>
            <th className="pb-2 text-right font-medium">Shrunk β</th>
            <th className="pb-2 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {diag.names.map((n) => (
            <tr key={n.ticker} className="border-t border-ocean-border/60">
              <td className="py-2 font-mono">{n.ticker}</td>
              <td className="py-2 text-right font-mono">{n.bars ?? "—"}</td>
              <td className="py-2 font-mono text-xs text-ocean-muted">
                {n.first_bar ? `${n.first_bar} → ${n.last_bar}` : "—"}
                {n.starts_after_benchmark ? " ⚠" : ""}
              </td>
              <td className="py-2 text-right font-mono">{n.obs_used ?? "—"}</td>
              <td className={`py-2 text-right font-mono ${n.gap_days ? "text-status-watch" : ""}`}>
                {n.gap_days ?? "—"}
              </td>
              <td className="py-2 text-right font-mono">{n.beta_raw?.toFixed(2) ?? "—"}</td>
              <td className="py-2 text-right font-mono">{n.beta?.toFixed(2) ?? "—"}</td>
              <td
                className={`py-2 text-xs ${
                  n.status === "ok" ? "text-status-ok" : "text-status-breach"
                }`}
                title={n.note ?? ""}
              >
                {n.status}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

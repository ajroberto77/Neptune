import type { StressReport } from "../types";
import { money, pnlColor, signedMoney } from "../format";

const METHOD_LABELS: Record<string, string> = {
  parametric: "Parametric",
  historical: "Historical simulation",
  monte_carlo: "Monte Carlo",
};

/** Stress tab: scenario shocks (P&L impact split by book) and VaR/ES (3 methods). */
export function Stress({
  report,
  onRun,
  loading,
}: {
  report: StressReport | null;
  onRun: () => void;
  loading: boolean;
}) {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <p className="text-sm text-ocean-muted">
          Shock the book against market and factor moves, and measure tail risk.
        </p>
        <button
          onClick={onRun}
          disabled={loading}
          className="rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-ocean-accent/80 disabled:opacity-50"
        >
          {loading ? "Running…" : "Run stress"}
        </button>
      </div>

      {report ? (
        <>
          <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
            <h3 className="mb-3 font-display text-sm uppercase tracking-wide text-ocean-muted">
              Value at Risk{" "}
              <span className="text-ocean-muted/60">
                ({(report.var.confidence * 100).toFixed(0)}%, {report.var.horizon_days}d)
              </span>
            </h3>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-ocean-muted">
                  <th className="pb-2 font-medium">Method</th>
                  <th className="pb-2 text-right font-medium">VaR</th>
                  <th className="pb-2 text-right font-medium">Expected shortfall</th>
                  <th className="pb-2 text-right font-medium">1σ vol</th>
                  <th className="pb-2 text-right font-medium">Observations</th>
                </tr>
              </thead>
              <tbody>
                {report.var_methods.map((m) => (
                  <tr key={m.method} className="border-t border-ocean-border/60">
                    <td className="py-2">{METHOD_LABELS[m.method] ?? m.method}</td>
                    <td className="py-2 text-right font-mono text-status-breach">
                      {money(m.var)}
                    </td>
                    <td className="py-2 text-right font-mono text-status-breach">
                      {money(m.expected_shortfall)}
                    </td>
                    <td className="py-2 text-right font-mono">{money(m.volatility)}</td>
                    <td className="py-2 text-right font-mono text-ocean-muted">
                      {m.n_observations > 0 ? m.n_observations.toLocaleString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-3 text-xs text-ocean-muted">
              Three estimates of potential loss over the horizon — parametric
              (variance-covariance), historical simulation, and Monte Carlo.
            </p>
          </div>

          <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
            <h3 className="mb-3 font-display text-sm uppercase tracking-wide text-ocean-muted">
              Scenario Shocks
            </h3>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-ocean-muted">
                  <th className="pb-2 font-medium">Scenario</th>
                  <th className="pb-2 text-right font-medium">Long</th>
                  <th className="pb-2 text-right font-medium">Systematic</th>
                  <th className="pb-2 text-right font-medium">Discretionary</th>
                  <th className="pb-2 text-right font-medium">Total P&L</th>
                </tr>
              </thead>
              <tbody>
                {report.scenarios.map((s) => (
                  <tr key={s.name} className="border-t border-ocean-border/60">
                    <td className="py-2">{s.name}</td>
                    <Cell value={s.by_book.LONG ?? 0} />
                    <Cell value={s.by_book.SYSTEMATIC_SHORT ?? 0} />
                    <Cell value={s.by_book.DISCRETIONARY_SHORT ?? 0} />
                    <td className={`py-2 text-right font-mono font-medium ${pnlColor(s.total_pnl)}`}>
                      {signedMoney(s.total_pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-3 text-xs text-ocean-muted/70">
              Market shocks use each name's <span className="text-slate-300">downside beta</span> when
              the market falls and <span className="text-slate-300">upside beta</span> when it rises
              (fit on down vs up days), so a selloff and a rally aren't mirror images. The net-beta
              limit still uses the single pipeline beta.
            </p>
          </div>
        </>
      ) : (
        <p className="text-sm text-ocean-muted">
          Run stress to see scenario P&L and VaR.
        </p>
      )}
    </div>
  );
}

function Cell({ value }: { value: number }) {
  return (
    <td className={`py-2 text-right font-mono ${pnlColor(value)}`}>{signedMoney(value)}</td>
  );
}

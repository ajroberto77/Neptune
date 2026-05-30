import type { StressReport } from "../types";
import { money, pnlColor, signedMoney } from "../format";

/** Stress tab: scenario shocks (P&L impact split by book) and parametric VaR/ES. */
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
              Value at Risk
            </h3>
            <div className="flex flex-wrap gap-8">
              <Stat
                label={`VaR (${(report.var.confidence * 100).toFixed(0)}%, ${report.var.horizon_days}d)`}
                value={money(report.var.var)}
                tone="text-status-breach"
              />
              <Stat
                label="Expected shortfall"
                value={money(report.var.expected_shortfall)}
                tone="text-status-breach"
              />
              <Stat label="1σ volatility" value={money(report.var.volatility)} />
            </div>
            <p className="mt-3 text-xs text-ocean-muted">
              Parametric (variance-covariance) estimate — potential loss over the horizon.
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

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="text-xs uppercase text-ocean-muted">{label}</div>
      <div className={`font-mono text-lg ${tone ?? ""}`}>{value}</div>
    </div>
  );
}

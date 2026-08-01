import type { Frontier } from "../types";

/** Complexity-quality frontier: how hedge quality (tracking error) and beta neutrality
 * change as the position-count cap (N) is tightened. */
export function FrontierPanel({
  frontier,
  onRun,
  loading,
}: {
  frontier: Frontier | null;
  onRun: () => void;
  loading: boolean;
}) {
  const maxTE = frontier
    ? Math.max(...frontier.frontier.map((p) => p.tracking_error), 1e-9)
    : 1;

  return (
    <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
      <div className="flex items-center justify-between">
        <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
          Complexity-Quality Frontier
        </h3>
        <button
          onClick={onRun}
          disabled={loading}
          className="rounded border border-ocean-accent px-3 py-1.5 text-sm font-medium text-ocean-accent hover:bg-ocean-accent/10 disabled:opacity-50"
        >
          {loading ? "Running…" : "Run frontier"}
        </button>
      </div>

      {frontier ? (
        <table className="mt-4 w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-ocean-muted">
              <th className="pb-2 font-medium">Cap (N)</th>
              <th className="pb-2 text-right font-medium">Names</th>
              <th className="pb-2 text-right font-medium">Net &beta;</th>
              <th className="pb-2 font-medium">Tracking error</th>
              <th className="pb-2 text-right font-medium">&beta; &le; tol</th>
            </tr>
          </thead>
          <tbody>
            {frontier.frontier.map((p) => (
              <tr key={p.n_cap} className="border-t border-ocean-border/70">
                <td className="py-2 font-mono">&le; {p.n_cap}</td>
                <td className="py-2 text-right font-mono">{p.n_selected}</td>
                <td className="py-2 text-right font-mono">{p.net_beta_after.toFixed(4)}</td>
                <td className="py-2">
                  <div className="flex items-center gap-2">
                    <div className="h-2 flex-1 rounded-full bg-ocean-bg">
                      <div
                        className="h-2 rounded-full bg-ocean-secondary"
                        style={{ width: `${(p.tracking_error / maxTE) * 100}%` }}
                      />
                    </div>
                    <span className="w-16 text-right font-mono text-xs">
                      {p.tracking_error.toFixed(4)}
                    </span>
                  </div>
                </td>
                <td className="py-2 text-right">
                  <span
                    className={p.beta_within_tol ? "text-status-ok" : "text-status-breach"}
                  >
                    {p.beta_within_tol ? "yes" : "no"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="mt-4 text-sm text-ocean-muted">
          Run the frontier to compare capped hedges (fewer names = higher tracking error).
        </p>
      )}
    </div>
  );
}

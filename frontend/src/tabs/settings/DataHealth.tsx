import { useEffect, useState } from "react";
import type { SecuritiesHealth } from "../../api/client";
import { fetchSecuritiesHealth } from "../../api/client";
import { Card, CardTitle, Metric } from "./primitives";

/** Data health: benchmark bar count, universe coverage, and factor-panel status — the silent
 *  gates that decide whether betas and the hedge are trustworthy, made visible. */
export function DataHealth() {
  const [h, setH] = useState<SecuritiesHealth | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function load() {
    setLoading(true);
    setErr(null);
    fetchSecuritiesHealth()
      .then(setH)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  const bars = h?.benchmark_bars;
  const benchOk = bars != null && bars >= 200; // ~250 trading days = healthy benchmark
  const factorsLoaded = h?.factor_panel && h.factor_panel !== "MKT-only";

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <CardTitle>Data health</CardTitle>
        <button
          onClick={load}
          disabled={loading}
          className="rounded border border-ocean-border px-2 py-1 text-xs text-ocean-muted hover:text-slate-200 disabled:opacity-40"
        >
          {loading ? "Checking…" : "Refresh"}
        </button>
      </div>

      {err && <p className="text-sm text-status-breach">{err}</p>}
      {h && (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Metric
              label={`Benchmark (${h.benchmark ?? "?"}) bars`}
              value={bars ?? "—"}
              ok={benchOk}
              hint={benchOk ? undefined : "needs full backfill (~250)"}
            />
            <Metric
              label="Names projected"
              value={h.securities_projected}
              ok={h.securities_projected > 0}
            />
            <Metric
              label="Names with a beta"
              value={h.names_with_computable_beta ?? 0}
              ok={(h.names_with_computable_beta ?? 0) > 0}
              hint={`${h.names_with_30plus_bars} have ≥30 bars`}
            />
            <Metric
              label="Factor panel"
              value={h.factor_panel ?? "—"}
              ok={!!factorsLoaded}
              hint={factorsLoaded ? undefined : "load Ken French to enable factor hedges"}
            />
          </div>
          <p className="mt-3 text-xs text-ocean-muted">
            Source: <span className="font-mono">{h.source}</span> — {h.reason}
          </p>
        </>
      )}
    </Card>
  );
}

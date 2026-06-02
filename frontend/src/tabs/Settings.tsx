import { useEffect, useState } from "react";
import type { ConnectionRow } from "../types";
import type { SecuritiesHealth } from "../api/client";
import {
  fetchConnections,
  fetchSecuritiesHealth,
  ingestFactors,
  ingestPrices,
  saveConnection,
  syncUniverse,
  testConnection,
} from "../api/client";

const ROLE_LABELS: Record<string, string> = {
  PORTFOLIO: "Portfolio DB (app)",
  SECURITIES: "Securities DB (market data)",
  UNIVERSE: "Universe DB (cato_securities, read-only)",
};

type Form = {
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  sslmode: string;
};

const EMPTY: Form = {
  host: "",
  port: 5432,
  database: "",
  username: "",
  password: "",
  sslmode: "",
};

function rowToForm(row: ConnectionRow): Form {
  return {
    host: row.host ?? "",
    port: row.port ?? 5432,
    database: row.database ?? "",
    username: row.username ?? "",
    password: "", // never returned by the API; blank = leave unchanged on save
    sslmode: row.sslmode ?? "",
  };
}

/** Settings tab: point Neptune at the right database instances. The password field is
 *  write-only — left blank, it preserves the stored secret. */
export function Settings() {
  const [rows, setRows] = useState<ConnectionRow[]>([]);
  const [forms, setForms] = useState<Record<string, Form>>({});
  const [status, setStatus] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [oneTicker, setOneTicker] = useState("");

  function load() {
    fetchConnections()
      .then((rs) => {
        setRows(rs);
        const f: Record<string, Form> = {};
        for (const r of rs) f[r.role] = r.configured ? rowToForm(r) : { ...EMPTY };
        setForms(f);
      })
      .catch((e) => setError(String(e)));
  }

  useEffect(load, []);

  function update(role: string, patch: Partial<Form>) {
    setForms((prev) => ({ ...prev, [role]: { ...prev[role], ...patch } }));
  }

  async function handleSave(role: string) {
    setError(null);
    const f = forms[role];
    try {
      await saveConnection(role, {
        host: f.host,
        port: Number(f.port),
        database: f.database,
        username: f.username,
        // Blank password => omit, so the stored secret is preserved.
        password: f.password ? f.password : null,
        sslmode: f.sslmode || null,
      });
      setStatus((s) => ({ ...s, [role]: "Saved" }));
      load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleTest(role: string) {
    setStatus((s) => ({ ...s, [role]: "Testing…" }));
    try {
      const r = await testConnection(role);
      setStatus((s) => ({
        ...s,
        [role]: r.ok ? "Connection OK" : `Failed: ${r.error ?? "error"}`,
      }));
    } catch (e) {
      setStatus((s) => ({ ...s, [role]: String(e) }));
    }
  }

  async function handleSync() {
    setError(null);
    try {
      const r = await syncUniverse();
      setStatus((s) => ({
        ...s,
        UNIVERSE: `Synced ${r.synced} securities from ${r.source}`,
      }));
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleIngest(tickers?: string[]) {
    const label = tickers?.length ? tickers.join(", ") : "all names";
    setStatus((s) => ({ ...s, SECURITIES: `Backfilling prices (${label})…` }));
    try {
      const r = await ingestPrices(tickers);
      const bars = r.ingested.reduce((n, row) => n + row.prices, 0);
      setStatus((s) => ({
        ...s,
        SECURITIES: `Ingested ${bars} price bars across ${r.ingested.length} names (${r.start} → ${r.end})`,
      }));
    } catch (e) {
      setStatus((s) => ({ ...s, SECURITIES: String(e) }));
    }
  }

  async function handleFactors() {
    setStatus((s) => ({ ...s, SECURITIES: "Backfilling Ken French factors…" }));
    try {
      const r = await ingestFactors();
      const total = Object.values(r.counts).reduce((n, c) => n + c, 0);
      const names = Object.keys(r.counts).join(", ") || "none";
      setStatus((s) => ({
        ...s,
        SECURITIES: `Ingested ${total} factor observations (${names}) for ${r.start} → ${r.end}`,
      }));
    } catch (e) {
      setStatus((s) => ({ ...s, SECURITIES: String(e) }));
    }
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-ocean-muted">
        Point Neptune at the right database instances. A saved connection (host and port
        included) overrides the matching environment variable. The portfolio database is the
        bootstrap — it stores these settings, so it's set via <code>.env</code> and applied
        at startup. The password is write-only: leave it blank to keep the stored secret.
      </p>

      {error && (
        <div className="rounded border border-status-breach/40 bg-status-breach/10 p-3 text-sm text-status-breach">
          {error}
        </div>
      )}

      <DataHealth />

      {rows.map((row) => {
        const f = forms[row.role] ?? EMPTY;
        return (
          <div
            key={row.role}
            className="rounded-lg border border-ocean-border bg-ocean-panel p-5"
          >
            <div className="mb-3 flex items-center justify-between">
              <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
                {ROLE_LABELS[row.role] ?? row.role}
              </h3>
              {row.bootstrap && (
                <span className="rounded bg-ocean-accent/20 px-2 py-0.5 text-xs text-ocean-accent">
                  bootstrap · applies on restart
                </span>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <Field label="Host">
                <input
                  className="np-input"
                  value={f.host}
                  onChange={(e) => update(row.role, { host: e.target.value })}
                />
              </Field>
              <Field label="Port">
                <input
                  className="np-input"
                  type="number"
                  value={f.port}
                  onChange={(e) => update(row.role, { port: Number(e.target.value) })}
                />
              </Field>
              <Field label="Database">
                <input
                  className="np-input"
                  value={f.database}
                  onChange={(e) => update(row.role, { database: e.target.value })}
                />
              </Field>
              <Field label="Username">
                <input
                  className="np-input"
                  value={f.username}
                  onChange={(e) => update(row.role, { username: e.target.value })}
                />
              </Field>
              <Field
                label={row.has_password ? "Password (stored)" : "Password"}
              >
                <input
                  className="np-input"
                  type="password"
                  placeholder={row.has_password ? "•••••• (unchanged)" : ""}
                  value={f.password}
                  onChange={(e) => update(row.role, { password: e.target.value })}
                />
              </Field>
              <Field label="SSL mode">
                <input
                  className="np-input"
                  placeholder="(optional)"
                  value={f.sslmode}
                  onChange={(e) => update(row.role, { sslmode: e.target.value })}
                />
              </Field>
            </div>

            <div className="mt-4 flex items-center gap-2">
              <button
                onClick={() => handleSave(row.role)}
                className="rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-ocean-accent/80"
              >
                Save
              </button>
              <button
                onClick={() => handleTest(row.role)}
                className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200"
              >
                Test connection
              </button>
              {row.role === "UNIVERSE" && (
                <button
                  onClick={handleSync}
                  className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200"
                >
                  Sync universe
                </button>
              )}
              {row.role === "SECURITIES" && (
                <>
                  <button
                    onClick={() => handleIngest()}
                    className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200"
                  >
                    Backfill prices
                  </button>
                  <button
                    onClick={handleFactors}
                    className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200"
                  >
                    Backfill factors
                  </button>
                  <input
                    value={oneTicker}
                    onChange={(e) => setOneTicker(e.target.value.toUpperCase())}
                    placeholder="WEN"
                    aria-label="backfill-one-ticker"
                    className="np-input w-24"
                  />
                  <button
                    onClick={() =>
                      handleIngest(
                        oneTicker
                          .split(/[,\s]+/)
                          .map((t) => t.trim())
                          .filter(Boolean),
                      )
                    }
                    disabled={!oneTicker.trim()}
                    className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200 disabled:opacity-50"
                  >
                    Backfill one
                  </button>
                </>
              )}
              {status[row.role] && (
                <span className="text-sm text-ocean-muted">{status[row.role]}</span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Data health: benchmark bar count, universe coverage, and factor-panel status — the silent
 *  gates that decide whether betas and the hedge are trustworthy, made visible. */
function DataHealth() {
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
    <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
          Data health
        </h3>
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
            <Metric label="Names projected" value={h.securities_projected} ok={h.securities_projected > 0} />
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
          <p className="mt-3 text-xs text-ocean-muted/80">
            Source: <span className="font-mono">{h.source}</span> — {h.reason}
          </p>
        </>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  ok,
  hint,
}: {
  label: string;
  value: string | number;
  ok: boolean;
  hint?: string;
}) {
  return (
    <div>
      <div className="text-xs uppercase text-ocean-muted">{label}</div>
      <div className={`font-mono text-lg ${ok ? "text-status-ok" : "text-status-breach"}`}>
        {value}
      </div>
      {hint && <div className="text-[11px] text-ocean-muted/70">{hint}</div>}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-ocean-muted">{label}</span>
      {children}
    </label>
  );
}

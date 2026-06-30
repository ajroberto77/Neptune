import { useEffect, useState } from "react";
import type { BetaDiagnostics, ConnectionRow, CredentialRow } from "../types";
import type {
  EntityRow,
  FirmRow,
  MacroCatalogRow,
  PersonRow,
  PortfolioMeta,
  SecuritiesHealth,
} from "../api/client";
import {
  createPortfolio,
  deletePortfolio,
  fetchBetaDiagnostics,
  fetchConnections,
  fetchCredentials,
  fetchEntities,
  fetchFirms,
  fetchMacroCatalog,
  fetchPeople,
  fetchPortfolios,
  fetchSecuritiesHealth,
  ingestFactors,
  ingestMacro,
  ingestPrices,
  saveConnection,
  saveCredential,
  syncUniverse,
  testConnection,
} from "../api/client";

const ROLE_LABELS: Record<string, string> = {
  PORTFOLIO: "Portfolio DB (app)",
  SECURITIES: "Securities DB (market data)",
  MACRO: "Macro DB (rates/credit + economic data)",
  UNIVERSE: "Universe DB (cato_securities, read-only)",
};

const PROVIDER_LABELS: Record<string, string> = {
  FRED: "FRED / ALFRED — macro data (rates, credit, economic; one key serves both)",
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

interface SettingsProps {
  onPortfoliosChanged?: () => void;
  /** Test Mode is only possible inside the Electron shell (it relaunches the backend). */
  canTestMode?: boolean;
  inTestMode?: boolean;
  /** Whether the core data load succeeds: false = no reachable DB, null = not determined yet. */
  dbReachable?: boolean | null;
  switchingMode?: boolean;
  onStartTestMode?: () => void;
  onStopTestMode?: () => void;
  // Server price-refresh controls (moved here from the Portfolio tab).
  refreshMins?: number;
  onChangeMins?: (m: number) => void;
  onRefreshNow?: () => void;
  lastPriced?: string | null;
  pricing?: boolean;
}

/** Settings tab: point Neptune at the right database instances. The password field is
 *  write-only — left blank, it preserves the stored secret. ``onPortfoliosChanged`` lets the
 *  app refresh its book switcher after a book is added or removed here. */
export function Settings({
  onPortfoliosChanged,
  canTestMode = false,
  inTestMode = false,
  dbReachable = null,
  switchingMode = false,
  onStartTestMode,
  onStopTestMode,
  refreshMins,
  onChangeMins,
  onRefreshNow,
  lastPriced,
  pricing,
}: SettingsProps = {}) {
  const [rows, setRows] = useState<ConnectionRow[]>([]);
  const [forms, setForms] = useState<Record<string, Form>>({});
  const [status, setStatus] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [oneTicker, setOneTicker] = useState("");
  const [years, setYears] = useState(7); // backfill depth (more = deeper backtest history)
  const [betaDiag, setBetaDiag] = useState<BetaDiagnostics | null>(null);
  const [creds, setCreds] = useState<CredentialRow[]>([]);
  const [keyInputs, setKeyInputs] = useState<Record<string, string>>({});
  const [keyStatus, setKeyStatus] = useState<Record<string, string>>({});
  const [catalog, setCatalog] = useState<MacroCatalogRow[]>([]);

  // Electron IPC-based DB config: reads/writes the local neptune-config.json in Electron's
  // userData directory, which determines which Postgres the backend sidecar connects to.
  // This path works even when the backend is completely offline (no chicken-and-egg problem).
  type DbForm = { host: string; port: number; database: string; user: string; password: string };
  const [electronCfg, setElectronCfg] = useState<Record<string, unknown> | null>(null);
  const [electronForms, setElectronForms] = useState<Record<string, DbForm>>({});
  const [electronDbStatus, setElectronDbStatus] = useState<Record<string, string>>({});
  const isElectron = typeof window !== "undefined" && Boolean(window.neptune?.saveConfig);

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

  function loadCreds() {
    fetchCredentials()
      .then(setCreds)
      .catch((e) => setError(String(e)));
  }

  function loadCatalog() {
    fetchMacroCatalog()
      .then((c) => setCatalog(c.series))
      .catch(() => setCatalog([])); // catalog is informational — don't blank the page on failure
  }

  useEffect(() => {
    load();
    loadCreds();
    loadCatalog();
  }, []);

  useEffect(() => {
    if (!isElectron) return;
    window.neptune!.getConfig().then((cfg) => {
      setElectronCfg(cfg);
      const toForm = (key: string): DbForm => {
        const d = (cfg as Record<string, Record<string, unknown>>)[key] ?? {};
        return {
          host: (d.host as string) ?? "localhost",
          port: Number(d.port ?? 5432),
          database: (d.database as string) ?? "",
          user: (d.user as string) ?? "postgres",
          password: (d.password as string) ?? "",
        };
      };
      setElectronForms({
        portfolio: toForm("portfolioDb"),
        securities: toForm("securitiesDb"),
        macro: toForm("macroDb"),
        universe: toForm("universeDb"),
      });
    }).catch(() => {});
  }, [isElectron]);

  async function handleSaveKey(provider: string) {
    setError(null);
    try {
      const st = await saveCredential(provider, { api_key: keyInputs[provider] ?? "" });
      setKeyStatus((s) => ({ ...s, [provider]: st.has_key ? "Key saved" : "Key cleared" }));
      setKeyInputs((s) => ({ ...s, [provider]: "" })); // never keep the secret in component state
      loadCreds();
    } catch (e) {
      setError(String(e));
    }
  }

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
    setStatus((s) => ({ ...s, SECURITIES: `Backfilling ${years}y of prices (${label})…` }));
    try {
      const r = await ingestPrices(tickers, years);
      const bars = r.ingested.reduce((n, row) => n + row.prices, 0);
      setStatus((s) => ({
        ...s,
        SECURITIES: `Ingested ${bars} price bars across ${r.ingested.length} names (${r.start} → ${r.end})`,
      }));
    } catch (e) {
      setStatus((s) => ({ ...s, SECURITIES: String(e) }));
    }
  }

  async function handleDiagnose(tickers: string[]) {
    if (!tickers.length) return;
    setStatus((s) => ({ ...s, SECURITIES: `Diagnosing ${tickers.join(", ")}…` }));
    setBetaDiag(null);
    try {
      const r = await fetchBetaDiagnostics(tickers);
      setBetaDiag(r);
      setStatus((s) => ({ ...s, SECURITIES: "" }));
    } catch (e) {
      setStatus((s) => ({ ...s, SECURITIES: String(e) }));
    }
  }

  async function handleMacroIngest() {
    setStatus((s) => ({ ...s, MACRO: "Backfilling macro series since 2000…" }));
    try {
      const r = await ingestMacro(2000);
      setStatus((s) => ({
        ...s,
        MACRO: `Ingested ${r.total} points across ${r.series} macro series`,
      }));
      loadCreds();
      loadCatalog(); // refresh coverage so the catalog table shows the new points/last-date
    } catch (e) {
      setStatus((s) => ({ ...s, MACRO: String(e) }));
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

  async function handleElectronSave(role: string) {
    if (!electronCfg) return;
    const cfgKey = (
      { portfolio: "portfolioDb", securities: "securitiesDb", macro: "macroDb", universe: "universeDb" } as Record<string, string>
    )[role];
    const form = electronForms[role];
    const newCfg = { ...electronCfg, [cfgKey]: { ...form, port: Number(form.port) } };
    setElectronDbStatus((s) => ({ ...s, [role]: "Saving…" }));
    try {
      await window.neptune!.saveConfig(newCfg);
      setElectronCfg(newCfg);
      setElectronDbStatus((s) => ({ ...s, [role]: "Saved — backend restarting…" }));
      setTimeout(() => setElectronDbStatus((s) => ({ ...s, [role]: "" })), 5000);
    } catch (e) {
      setElectronDbStatus((s) => ({ ...s, [role]: `Error: ${String(e)}` }));
    }
  }

  async function handleElectronTest(role: string) {
    const form = electronForms[role];
    if (!form) return;
    setElectronDbStatus((s) => ({ ...s, [role]: "Testing…" }));
    try {
      const result = await window.neptune!.testDbConnection({ ...form, port: Number(form.port) });
      setElectronDbStatus((s) => ({
        ...s,
        [role]: result.ok ? "Connection OK" : `Failed: ${result.message}`,
      }));
    } catch (e) {
      setElectronDbStatus((s) => ({ ...s, [role]: String(e) }));
    }
  }

  function updateElectronForm(role: string, patch: Partial<DbForm>) {
    setElectronForms((p) => ({ ...p, [role]: { ...p[role], ...patch } }));
  }

  return (
    <div className="space-y-6">
      {/* Test Mode — surfaces only in the Electron shell, and only when relevant: either no
          database is reachable (offer it) or it's already active (offer a way out). It relaunches
          the backend on a throwaway SQLite DB with a seeded demo book + synthetic market data. */}
      {canTestMode && inTestMode ? (
        <div className="flex items-center justify-between gap-4 rounded-lg border border-status-watch/40 bg-status-watch/10 p-4">
          <div>
            <p className="font-display text-sm font-semibold text-status-watch">Test Mode is active</p>
            <p className="mt-1 text-xs text-slate-300">
              Running on a throwaway local database with synthetic demo data — nothing here is real.
            </p>
          </div>
          <button
            onClick={onStopTestMode}
            disabled={switchingMode}
            className="shrink-0 rounded border border-ocean-border px-3 py-1.5 text-sm text-slate-200 transition hover:border-ocean-accent hover:text-white disabled:opacity-50"
          >
            {switchingMode ? "Switching…" : "Exit Test Mode"}
          </button>
        </div>
      ) : canTestMode && dbReachable === false ? (
        <div className="flex items-center justify-between gap-4 rounded-lg border border-ocean-accent/40 bg-ocean-accent/10 p-4">
          <div>
            <p className="font-display text-sm font-semibold text-white">No database connection found</p>
            <p className="mt-1 text-xs text-slate-300">
              Can&apos;t reach a database, so risk and positions won&apos;t load. Explore the app in
              Test Mode — a throwaway local database seeded with a demo book and synthetic data.
            </p>
          </div>
          <button
            onClick={onStartTestMode}
            disabled={switchingMode}
            className="shrink-0 rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white transition hover:bg-ocean-accent/80 disabled:opacity-50"
          >
            {switchingMode ? "Starting…" : "Enable Test Mode"}
          </button>
        </div>
      ) : null}

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

      <PortfoliosPanel onChanged={onPortfoliosChanged} />

      {/* Server price refresh — how often the backend pulls fresh marks for the open books.
          "Live" streaming arrives with the Bloomberg feed (disabled until then). */}
      {onChangeMins && (
        <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
          <h3 className="mb-1 font-display text-sm uppercase tracking-wide text-ocean-muted">
            Price refresh
          </h3>
          <p className="mb-3 text-xs text-ocean-muted">
            How often the server re-prices the open books. Manual refresh re-prices the selected
            book immediately.
          </p>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <select
              value={String(refreshMins ?? 0)}
              onChange={(e) => onChangeMins?.(Number(e.target.value))}
              className="np-input w-auto"
              aria-label="price-refresh-interval"
            >
              <option value="0">Off</option>
              <option value="1">Every 1 min</option>
              <option value="5">Every 5 min</option>
              <option value="10">Every 10 min</option>
              <option value="15">Every 15 min</option>
              <option value="30">Every 30 min</option>
              <option value="live" disabled>
                Live (Bloomberg — coming soon)
              </option>
            </select>
            <button
              onClick={onRefreshNow}
              disabled={pricing}
              className="rounded border border-ocean-border px-3 py-1.5 text-ocean-muted transition hover:text-slate-200 disabled:opacity-50"
            >
              {pricing ? "Refreshing…" : "Refresh now"}
            </button>
            {lastPriced && (
              <span className="text-xs text-ocean-muted/60">updated {lastPriced}</span>
            )}
          </div>
        </div>
      )}

      <DataHealth />

      <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
        <h3 className="mb-1 font-display text-sm uppercase tracking-wide text-ocean-muted">
          Data provider API keys
        </h3>
        <p className="mb-3 text-xs text-ocean-muted">
          Keys for external data feeds. FRED powers the macro database (rates, credit, economic
          data); the same free key serves ALFRED (point-in-time vintages). Get one at{" "}
          <a
            href="https://fredaccount.stlouisfed.org/apikeys"
            target="_blank"
            rel="noreferrer"
            className="text-ocean-accent hover:underline"
          >
            fredaccount.stlouisfed.org/apikeys
          </a>
          . Keys are write-only — leave blank to keep the stored secret.
        </p>
        <div className="space-y-3">
          {creds.map((c) => (
            <div key={c.provider} className="flex flex-wrap items-end gap-2">
              <div className="min-w-[18rem] flex-1">
                <Field
                  label={`${PROVIDER_LABELS[c.provider] ?? c.provider} ${
                    c.has_key ? `· set (${c.source})` : "· not set"
                  }`}
                >
                  <input
                    className="np-input"
                    type="password"
                    aria-label={`${c.provider}-api-key`}
                    placeholder={c.has_key ? "•••••• (stored)" : "paste API key"}
                    value={keyInputs[c.provider] ?? ""}
                    onChange={(e) =>
                      setKeyInputs((s) => ({ ...s, [c.provider]: e.target.value }))
                    }
                  />
                </Field>
              </div>
              <button
                onClick={() => handleSaveKey(c.provider)}
                className="rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-ocean-accent/80"
              >
                Save key
              </button>
              {keyStatus[c.provider] && (
                <span className="text-sm text-ocean-muted">{keyStatus[c.provider]}</span>
              )}
            </div>
          ))}
        </div>
      </div>

      {catalog.length > 0 && (
        <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
          <h3 className="mb-1 font-display text-sm uppercase tracking-wide text-ocean-muted">
            Macro series catalog ({catalog.length})
          </h3>
          <p className="mb-3 text-xs text-ocean-muted">
            Every series the macro backfill can pull (FRED for market/rates/credit, ALFRED for
            point-in-time economic vintages). "Backfill macro" on the Macro DB card below loads
            them since 2000. "Loaded" shows what's already in your macro DB.
          </p>
          <div className="max-h-96 overflow-auto rounded border border-ocean-border">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-ocean-bg text-ocean-muted">
                <tr>
                  <th className="px-3 py-2">Series</th>
                  <th className="px-3 py-2">Name</th>
                  <th className="px-3 py-2">Category</th>
                  <th className="px-3 py-2">Freq</th>
                  <th className="px-3 py-2">Source</th>
                  <th className="px-3 py-2 text-right">Loaded</th>
                  <th className="px-3 py-2">Last date</th>
                </tr>
              </thead>
              <tbody>
                {catalog.map((s) => (
                  <tr key={s.series_id} className="border-t border-ocean-border/50">
                    <td className="px-3 py-1.5 font-mono text-ocean-text">{s.series_id}</td>
                    <td className="px-3 py-1.5 text-ocean-muted">{s.name}</td>
                    <td className="px-3 py-1.5 text-ocean-muted">{s.category}</td>
                    <td className="px-3 py-1.5 text-ocean-muted">{s.frequency}</td>
                    <td className="px-3 py-1.5 text-ocean-muted">
                      {s.ingestable ? (
                        <span className="font-mono">{s.source_code}</span>
                      ) : (
                        <span className="italic">{s.source} (not ingested)</span>
                      )}
                    </td>
                    <td
                      className={`px-3 py-1.5 text-right font-mono ${
                        s.points > 0 ? "text-status-ok" : "text-ocean-muted"
                      }`}
                    >
                      {s.points > 0 ? s.points.toLocaleString() : "—"}
                    </td>
                    <td className="px-3 py-1.5 font-mono text-ocean-muted">
                      {s.last_date ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {isElectron ? (
        /* In Electron mode the DB config is the local neptune-config.json (userData); saving it
           restarts the sidecar with the new URLs. This path works offline — no backend needed. */
        (
          [
            { role: "portfolio",  label: "Portfolio DB (app)",                       note: "applies on restart" },
            { role: "securities", label: "Securities DB (market data)",               note: undefined },
            { role: "macro",      label: "Macro DB (rates/credit + economic data)",   note: undefined },
            { role: "universe",   label: "Universe DB (cato_securities, read-only)",  note: undefined },
          ] as { role: string; label: string; note?: string }[]
        ).map(({ role, label, note }) => {
          const f = electronForms[role];
          if (!f) return null;
          return (
            <div key={role} className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
              <div className="mb-3 flex items-center justify-between">
                <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
                  {label}
                </h3>
                {note && (
                  <span className="rounded bg-ocean-accent/20 px-2 py-0.5 text-xs text-ocean-accent">
                    {note}
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <Field label="Host">
                  <input className="np-input" value={f.host}
                    onChange={(e) => updateElectronForm(role, { host: e.target.value })} />
                </Field>
                <Field label="Port">
                  <input className="np-input" type="number" value={f.port}
                    onChange={(e) => updateElectronForm(role, { port: Number(e.target.value) })} />
                </Field>
                <Field label="Database">
                  <input className="np-input" value={f.database}
                    onChange={(e) => updateElectronForm(role, { database: e.target.value })} />
                </Field>
                <Field label="Username">
                  <input className="np-input" value={f.user}
                    onChange={(e) => updateElectronForm(role, { user: e.target.value })} />
                </Field>
                <Field label="Password">
                  <input className="np-input" type="password" placeholder="(leave blank to clear)"
                    value={f.password}
                    onChange={(e) => updateElectronForm(role, { password: e.target.value })} />
                </Field>
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-2">
                <button onClick={() => handleElectronSave(role)}
                  className="rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-ocean-accent/80">
                  Save &amp; restart backend
                </button>
                <button onClick={() => handleElectronTest(role)}
                  className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200">
                  Test connection
                </button>
                {role === "universe" && (
                  <button onClick={handleSync}
                    className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200">
                    Sync universe
                  </button>
                )}
                {role === "macro" && (
                  <button onClick={handleMacroIngest}
                    className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200">
                    Backfill macro (FRED/ALFRED, since 2000)
                  </button>
                )}
                {role === "securities" && (
                  <>
                    <label className="flex items-center gap-1 text-xs text-ocean-muted">
                      <input type="number" min={1} max={25} value={years}
                        aria-label="backfill-years"
                        onChange={(e) => setYears(Math.max(1, Math.min(25, Number(e.target.value))))}
                        className="np-input w-16" />
                      yrs
                    </label>
                    <button onClick={() => handleIngest()}
                      className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200">
                      Backfill prices
                    </button>
                    <button onClick={handleFactors}
                      className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200">
                      Backfill factors
                    </button>
                    <input value={oneTicker}
                      onChange={(e) => setOneTicker(e.target.value.toUpperCase())}
                      placeholder="WEN" aria-label="backfill-one-ticker"
                      className="np-input w-24" />
                    <button onClick={() => handleIngest(parseTickers(oneTicker))}
                      disabled={!oneTicker.trim()}
                      className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200 disabled:opacity-50">
                      Backfill one
                    </button>
                    <button onClick={() => handleDiagnose(parseTickers(oneTicker))}
                      disabled={!oneTicker.trim()}
                      className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200 disabled:opacity-50">
                      Diagnose beta
                    </button>
                  </>
                )}
                {(electronDbStatus[role] || status[role === "universe" ? "UNIVERSE" : role === "macro" ? "MACRO" : role === "securities" ? "SECURITIES" : "PORTFOLIO"]) && (
                  <span className="text-sm text-ocean-muted">
                    {electronDbStatus[role] || status[role === "universe" ? "UNIVERSE" : role === "macro" ? "MACRO" : role === "securities" ? "SECURITIES" : "PORTFOLIO"]}
                  </span>
                )}
              </div>
            </div>
          );
        })
      ) : (
        rows.map((row) => {
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
                <div className="flex items-center gap-2">
                  {row.source === "env" && (
                    <span className="rounded bg-ocean-border/40 px-2 py-0.5 text-xs text-ocean-muted">
                      from .env
                    </span>
                  )}
                  {row.bootstrap && (
                    <span className="rounded bg-ocean-accent/20 px-2 py-0.5 text-xs text-ocean-accent">
                      bootstrap · applies on restart
                    </span>
                  )}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <Field label="Host">
                  <input className="np-input" value={f.host}
                    onChange={(e) => update(row.role, { host: e.target.value })} />
                </Field>
                <Field label="Port">
                  <input className="np-input" type="number" value={f.port}
                    onChange={(e) => update(row.role, { port: Number(e.target.value) })} />
                </Field>
                <Field label="Database">
                  <input className="np-input" value={f.database}
                    onChange={(e) => update(row.role, { database: e.target.value })} />
                </Field>
                <Field label="Username">
                  <input className="np-input" value={f.username}
                    onChange={(e) => update(row.role, { username: e.target.value })} />
                </Field>
                <Field label={row.has_password ? "Password (stored)" : "Password"}>
                  <input className="np-input" type="password"
                    placeholder={row.has_password ? "•••••• (unchanged)" : ""}
                    value={f.password}
                    onChange={(e) => update(row.role, { password: e.target.value })} />
                </Field>
                <Field label="SSL mode">
                  <input className="np-input" placeholder="(optional)" value={f.sslmode}
                    onChange={(e) => update(row.role, { sslmode: e.target.value })} />
                </Field>
              </div>
              <div className="mt-4 flex items-center gap-2">
                <button onClick={() => handleSave(row.role)}
                  className="rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-ocean-accent/80">
                  Save
                </button>
                <button onClick={() => handleTest(row.role)}
                  className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200">
                  Test connection
                </button>
                {row.role === "UNIVERSE" && (
                  <button onClick={handleSync}
                    className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200">
                    Sync universe
                  </button>
                )}
                {row.role === "MACRO" && (
                  <button onClick={handleMacroIngest}
                    className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200">
                    Backfill macro (FRED/ALFRED, since 2000)
                  </button>
                )}
                {row.role === "SECURITIES" && (
                  <>
                    <label className="flex items-center gap-1 text-xs text-ocean-muted">
                      <input type="number" min={1} max={25} value={years}
                        aria-label="backfill-years"
                        onChange={(e) => setYears(Math.max(1, Math.min(25, Number(e.target.value))))}
                        className="np-input w-16" />
                      yrs
                    </label>
                    <button onClick={() => handleIngest()}
                      className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200">
                      Backfill prices
                    </button>
                    <button onClick={handleFactors}
                      className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200">
                      Backfill factors
                    </button>
                    <input value={oneTicker}
                      onChange={(e) => setOneTicker(e.target.value.toUpperCase())}
                      placeholder="WEN" aria-label="backfill-one-ticker"
                      className="np-input w-24" />
                    <button onClick={() => handleIngest(parseTickers(oneTicker))}
                      disabled={!oneTicker.trim()}
                      className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200 disabled:opacity-50">
                      Backfill one
                    </button>
                    <button onClick={() => handleDiagnose(parseTickers(oneTicker))}
                      disabled={!oneTicker.trim()}
                      className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200 disabled:opacity-50">
                      Diagnose beta
                    </button>
                  </>
                )}
                {status[row.role] && (
                  <span className="text-sm text-ocean-muted">{status[row.role]}</span>
                )}
              </div>
            </div>
          );
        })
      )}

      {betaDiag && <BetaDiagPanel diag={betaDiag} />}
    </div>
  );
}

function parseTickers(s: string): string[] {
  return s
    .split(/[,\s]+/)
    .map((t) => t.trim().toUpperCase())
    .filter(Boolean);
}

/** Per-ticker beta diagnostics: the regression inputs behind a surprising beta — stored bars,
 *  date span vs the benchmark, observations used, forward-filled gap days, and raw vs shrunk
 *  beta — so "too low/high" can be traced to data vs a genuine fit. */
function BetaDiagPanel({ diag }: { diag: BetaDiagnostics }) {
  const b = diag.benchmark;
  return (
    <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
      <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">
        Beta diagnostics
      </h3>
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
    </div>
  );
}

/** Portfolios: add and remove books. A book carries full ownership — a management firm, the
 *  investor entity whose capital it runs, and its lead PM — picked from the org graph. Removing
 *  a book is refused by the server while it still holds open positions (flatten it first), and
 *  is bookkeeping only — it never routes or unwinds anything at a venue. */
function PortfoliosPanel({ onChanged }: { onChanged?: () => void }) {
  const [books, setBooks] = useState<PortfolioMeta[]>([]);
  const [firms, setFirms] = useState<FirmRow[]>([]);
  const [people, setPeople] = useState<PersonRow[]>([]);
  const [entities, setEntities] = useState<EntityRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [name, setName] = useState("");
  const [mandate, setMandate] = useState<"LONG_SHORT" | "LONG_ONLY">("LONG_SHORT");
  const [firmId, setFirmId] = useState("");
  const [entityId, setEntityId] = useState("");
  const [leadPm, setLeadPm] = useState("");

  function load() {
    fetchPortfolios().then(setBooks).catch((e) => setError(String(e)));
    fetchFirms().then(setFirms).catch(() => setFirms([]));
    fetchPeople().then(setPeople).catch(() => setPeople([]));
    fetchEntities().then(setEntities).catch(() => setEntities([]));
  }
  useEffect(load, []);

  const personName = (id?: string | null) =>
    id ? people.find((p) => p.id === id)?.name ?? id : "—";

  async function handleAdd() {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await createPortfolio({
        name: name.trim(),
        mandate,
        firm_id: firmId || null,
        investor_entity_id: entityId || null,
        lead_pm_ids: leadPm ? [leadPm] : [],
      });
      setName("");
      setLeadPm("");
      load();
      onChanged?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(id: string, label: string) {
    if (!window.confirm(`Remove portfolio "${label}"? This cannot be undone.`)) return;
    setBusy(true);
    setError(null);
    try {
      await deletePortfolio(id);
      load();
      onChanged?.();
    } catch (e) {
      // The server refuses a book that still holds open positions (409) — surface that.
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">
      <h3 className="mb-1 font-display text-sm uppercase tracking-wide text-ocean-muted">
        Portfolios
      </h3>
      <p className="mb-3 text-xs text-ocean-muted">
        The books Neptune manages. Each belongs to an investor entity and is led by a PM. A book
        must be flattened (no open positions) before it can be removed.
      </p>

      {error && (
        <div className="mb-3 rounded border border-status-breach/40 bg-status-breach/10 p-2 text-sm text-status-breach">
          {error}
        </div>
      )}

      {books.length === 0 ? (
        <p className="mb-4 text-sm text-status-watch">
          No portfolios yet — add your first book below to get started.
        </p>
      ) : (
        <div className="mb-4 overflow-auto rounded border border-ocean-border">
          <table className="w-full text-left text-sm">
            <thead className="bg-ocean-bg text-xs uppercase text-ocean-muted">
              <tr>
                <th className="px-3 py-2">Book</th>
                <th className="px-3 py-2">Mandate</th>
                <th className="px-3 py-2">Lead PM</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {books.map((b) => (
                <tr key={b.id} className="border-t border-ocean-border/50">
                  <td className="px-3 py-2">
                    <div className="text-ocean-text">{b.name}</div>
                    <div className="font-mono text-[11px] text-ocean-muted">{b.id}</div>
                  </td>
                  <td className="px-3 py-2 text-ocean-muted">
                    {b.mandate === "LONG_SHORT" ? "Long / Short" : "Long Only"}
                  </td>
                  <td className="px-3 py-2 text-ocean-muted">
                    {personName(b.lead_pm_ids?.[0])}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => handleRemove(b.id, b.name)}
                      disabled={busy}
                      className="rounded border border-status-breach/40 px-2 py-1 text-xs text-status-breach hover:bg-status-breach/10 disabled:opacity-50"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Field label="Name">
          <input
            className="np-input"
            aria-label="new-portfolio-name"
            placeholder="Macro Alpha"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="Mandate">
          <select
            className="np-input"
            aria-label="new-portfolio-mandate"
            value={mandate}
            onChange={(e) => setMandate(e.target.value as "LONG_SHORT" | "LONG_ONLY")}
          >
            <option value="LONG_SHORT">Long / Short (hedged)</option>
            <option value="LONG_ONLY">Long Only</option>
          </select>
        </Field>
        <Field label="Lead PM">
          <select
            className="np-input"
            aria-label="new-portfolio-pm"
            value={leadPm}
            onChange={(e) => setLeadPm(e.target.value)}
          >
            <option value="">(none)</option>
            {people.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Firm">
          <select
            className="np-input"
            aria-label="new-portfolio-firm"
            value={firmId}
            onChange={(e) => setFirmId(e.target.value)}
          >
            <option value="">(none)</option>
            {firms.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Investor entity">
          <select
            className="np-input"
            aria-label="new-portfolio-entity"
            value={entityId}
            onChange={(e) => setEntityId(e.target.value)}
          >
            <option value="">(none)</option>
            {entities.map((en) => (
              <option key={en.id} value={en.id}>
                {en.name}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <div className="mt-4">
        <button
          onClick={handleAdd}
          disabled={busy || !name.trim()}
          className="rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-ocean-accent/80 disabled:opacity-50"
        >
          Add portfolio
        </button>
      </div>
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

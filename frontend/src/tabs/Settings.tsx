import { useEffect, useState } from "react";
import type { BetaDiagnostics, ConnectionRow, CredentialRow } from "../types";
import type { MacroCatalogRow } from "../api/client";
import {
  fetchBetaDiagnostics,
  fetchConnections,
  fetchCredentials,
  fetchMacroCatalog,
  ingestFactors,
  ingestMacro,
  ingestPrices,
  saveConnection,
  saveCredential,
  syncUniverse,
  testConnection,
} from "../api/client";
import { BetaDiagPanel } from "./settings/BetaDiagPanel";
import { DataHealth } from "./settings/DataHealth";
import { DbFamilyCard, divergesFrom, familyBase } from "./settings/DbFamilyCard";
import { PortfoliosPanel } from "./settings/PortfoliosPanel";
import { Card, CardTitle, Field, SectionLabel } from "./settings/primitives";
import { ALL_MEMBERS, DB_FAMILIES, EMPTY_CONN } from "./settings/families";
import type { DbConn, DbFamily, DbRole } from "./settings/families";

const PROVIDER_LABELS: Record<string, string> = {
  FRED: "FRED / ALFRED — macro data (rates, credit, economic; one key serves both)",
};

type SectionId =
  | "general"
  | "databases"
  | "keys"
  | "portfolios"
  | "ingest"
  | "diagnostics";

const SECTIONS: { id: SectionId; label: string }[] = [
  { id: "general", label: "General" },
  { id: "databases", label: "Databases" },
  { id: "keys", label: "API Keys" },
  { id: "portfolios", label: "Portfolios" },
  { id: "ingest", label: "Data Ingest" },
  { id: "diagnostics", label: "Diagnostics" },
];

/** Which members already point somewhere other than their family's server, per the stored
 *  config. Seeds the override checkboxes so an existing split (CATO commonly runs on its own
 *  instance) shows up as such instead of being silently flattened onto the shared fields. */
function seedOwnServer(conns: Record<string, DbConn>): Set<string> {
  const out = new Set<string>();
  for (const family of DB_FAMILIES) {
    const base = familyBase(family, conns);
    for (const m of family.members.slice(1)) {
      if (divergesFrom(base, conns[m.role])) out.add(m.role);
    }
  }
  return out;
}

function rowToConn(row: ConnectionRow): DbConn {
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
  /** Leaves the Settings page. Rendered as the header's Close button when provided. */
  onClose?: () => void;
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

/** Settings: a sidebar-navigated config page. Databases are presented by family (Neptune's own
 *  vs CATO's read-only master) rather than by internal role; ingest actions live in their own
 *  section so they can move to the Iridium Backend without disturbing the rest. */
export function Settings({
  onPortfoliosChanged,
  onClose,
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
  const [section, setSection] = useState<SectionId>("general");

  const [rows, setRows] = useState<ConnectionRow[]>([]);
  /** One normalized form per role, whichever config path is live. */
  const [conns, setConns] = useState<Record<string, DbConn>>({});
  /** Roles edited since their last save — the API path can't test unsaved values. */
  const [dirtyRoles, setDirtyRoles] = useState<Set<string>>(new Set());
  /** Members broken out onto their own server. Seeded from the stored config's divergence,
   *  then owned by the checkbox — deriving it from the values would make the box un-check
   *  itself the moment it was ticked (a fresh override still matches the shared server). */
  const [ownServer, setOwnServer] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState("");
  /** Price-refresh interval waiting to be saved (null = unchanged). */
  const [pendingMins, setPendingMins] = useState<number | null>(null);

  const [ingestTicker, setIngestTicker] = useState("");
  const [diagTicker, setDiagTicker] = useState("");
  const [years, setYears] = useState(7); // backfill depth (more = deeper backtest history)
  const [betaDiag, setBetaDiag] = useState<BetaDiagnostics | null>(null);

  const [creds, setCreds] = useState<CredentialRow[]>([]);
  const [keyInputs, setKeyInputs] = useState<Record<string, string>>({});
  const [keyStatus, setKeyStatus] = useState<Record<string, string>>({});
  const [catalog, setCatalog] = useState<MacroCatalogRow[]>([]);

  // Electron IPC-based DB config: reads/writes the local neptune-config.json in Electron's
  // userData directory, which determines which Postgres the backend sidecar connects to.
  // This path works even when the backend is completely offline (no chicken-and-egg problem).
  const [electronCfg, setElectronCfg] = useState<Record<string, unknown> | null>(null);
  const isElectron = typeof window !== "undefined" && Boolean(window.neptune?.saveConfig);

  function load() {
    fetchConnections()
      .then((rs) => {
        setRows(rs);
        // In Electron the config file is the source of truth for the forms; the API rows are
        // still fetched for their badges (from .env / bootstrap), but must not clobber them.
        if (isElectron) return;
        const next: Record<string, DbConn> = {};
        for (const m of ALL_MEMBERS) next[m.role] = { ...EMPTY_CONN };
        for (const r of rs) if (r.configured) next[r.role] = rowToConn(r);
        setConns(next);
        setOwnServer(seedOwnServer(next));
        setDirtyRoles(new Set());
      })
      .catch((e) => {
        if (!isElectron) setError(String(e));
      });
  }

  function loadCreds() {
    fetchCredentials()
      .then(setCreds)
      .catch((e) => {
        if (!isElectron) setError(String(e));
      });
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
    window
      .neptune!.getConfig()
      .then((cfg) => {
        setElectronCfg(cfg);
        const next: Record<string, DbConn> = {};
        for (const m of ALL_MEMBERS) {
          const d = (cfg as Record<string, Record<string, unknown>>)[m.electronKey] ?? {};
          next[m.role] = {
            host: (d.host as string) ?? "localhost",
            port: Number(d.port ?? 5432),
            database: (d.database as string) ?? "",
            username: (d.user as string) ?? "postgres",
            password: (d.password as string) ?? "",
            sslmode: "", // not part of the Electron config
          };
        }
        setConns(next);
        setOwnServer(seedOwnServer(next));
        setDirtyRoles(new Set());
      })
      .catch(() => {});
  }, [isElectron]);

  function markDirty(roles: string[]) {
    setDirtyRoles((prev) => {
      const next = new Set(prev);
      for (const r of roles) next.add(r);
      return next;
    });
  }

  /** Edits every member that follows the family server, leaving broken-out ones alone. */
  function updateShared(family: DbFamily, patch: Partial<DbConn>) {
    const followers = family.members.filter((m) => !ownServer.has(m.role)).map((m) => m.role);
    setConns((prev) => {
      const next = { ...prev };
      for (const role of followers) next[role] = { ...(next[role] ?? EMPTY_CONN), ...patch };
      return next;
    });
    markDirty(followers);
  }

  function updateMember(role: DbRole, patch: Partial<DbConn>) {
    setConns((prev) => ({ ...prev, [role]: { ...(prev[role] ?? EMPTY_CONN), ...patch } }));
    markDirty([role]);
  }

  function toggleOverride(family: DbFamily, role: DbRole, own: boolean) {
    setOwnServer((prev) => {
      const next = new Set(prev);
      if (own) next.add(role);
      else next.delete(role);
      return next;
    });
    if (!own) {
      // Rejoining the family: adopt its server, keeping this database's own name.
      setConns((prev) => {
        const base = familyBase(family, prev);
        const mine = prev[role] ?? EMPTY_CONN;
        return {
          ...prev,
          [role]: {
            ...base,
            database: mine.database,
            password: "", // the shared password applies; don't carry the old one over
          },
        };
      });
    }
    markDirty([role]);
  }

  const dirtyKeyProviders = Object.keys(keyInputs).filter((p) => keyInputs[p]?.trim());
  const dirtyCount = dirtyRoles.size + dirtyKeyProviders.length + (pendingMins !== null ? 1 : 0);
  const isDirty = dirtyCount > 0;

  /** Persists everything edited on the page. Databases fan out across the roles that make up
   *  each family; in Electron they go in one config write (one backend restart) rather than
   *  one per database. Failures are reported per item — there is no bulk endpoint, so a
   *  partial save is possible and is better surfaced than hidden. */
  async function handleSaveAll() {
    if (!isDirty || saving) return;
    setSaving(true);
    setError(null);
    setSavedNote("");
    const failures: string[] = [];

    if (dirtyRoles.size > 0) {
      if (isElectron && electronCfg) {
        const newCfg: Record<string, unknown> = { ...electronCfg };
        for (const m of ALL_MEMBERS) {
          if (!dirtyRoles.has(m.role)) continue;
          const c = conns[m.role];
          if (!c) continue;
          newCfg[m.electronKey] = {
            host: c.host,
            port: Number(c.port),
            database: c.database,
            user: c.username,
            password: c.password,
          };
        }
        try {
          await window.neptune!.saveConfig(newCfg);
          setElectronCfg(newCfg);
        } catch (e) {
          failures.push(`Databases: ${String(e)}`);
        }
      } else {
        for (const m of ALL_MEMBERS) {
          if (!dirtyRoles.has(m.role)) continue;
          const c = conns[m.role];
          if (!c) continue;
          try {
            await saveConnection(m.role, {
              host: c.host,
              port: Number(c.port),
              database: c.database,
              username: c.username,
              // Blank => null so the stored secret survives; "" would clear it.
              password: c.password ? c.password : null,
              // sslmode is written unconditionally by the backend, so always resend it.
              sslmode: c.sslmode || null,
            });
          } catch (e) {
            failures.push(`${m.label}: ${String(e)}`);
          }
        }
      }
    }

    for (const provider of dirtyKeyProviders) {
      try {
        const st = await saveCredential(provider, { api_key: keyInputs[provider] });
        setKeyStatus((s) => ({ ...s, [provider]: st.has_key ? "Key saved" : "Key cleared" }));
        setKeyInputs((s) => ({ ...s, [provider]: "" })); // never keep the secret around
      } catch (e) {
        failures.push(`${provider} key: ${String(e)}`);
      }
    }

    if (pendingMins !== null) {
      try {
        await onChangeMins?.(pendingMins);
        setPendingMins(null);
      } catch (e) {
        failures.push(`Price refresh: ${String(e)}`);
      }
    }

    if (failures.length) {
      setError(`Some settings didn't save — ${failures.join("; ")}`);
    } else {
      setSavedNote(isElectron && dirtyRoles.size > 0 ? "Saved — backend restarting…" : "Saved");
      setTimeout(() => setSavedNote(""), 4000);
    }
    setDirtyRoles(new Set());
    setSaving(false);
    if (!isElectron) load();
    loadCreds();
  }

  function handleClose() {
    if (isDirty && !window.confirm("Discard unsaved settings changes?")) return;
    onClose?.();
  }

  async function handleTest(role: DbRole) {
    setStatus((s) => ({ ...s, [role]: "Testing…" }));
    if (isElectron) {
      // The Electron bridge tests the values it is handed, so it can check unsaved edits.
      const f = conns[role];
      if (!f) return;
      try {
        const result = await window.neptune!.testDbConnection({
          host: f.host,
          port: Number(f.port),
          database: f.database,
          user: f.username,
          password: f.password,
        });
        setStatus((s) => ({
          ...s,
          [role]: result.ok ? "Connection OK" : `Failed: ${result.message}`,
        }));
      } catch (e) {
        setStatus((s) => ({ ...s, [role]: String(e) }));
      }
      return;
    }
    try {
      const r = await testConnection(role);
      setStatus((s) => ({ ...s, [role]: r.ok ? "Connection OK" : `Failed: ${r.error ?? "error"}` }));
    } catch (e) {
      setStatus((s) => ({ ...s, [role]: String(e) }));
    }
  }

  async function handleSync() {
    setError(null);
    try {
      const r = await syncUniverse();
      setStatus((s) => ({ ...s, UNIVERSE: `Synced ${r.synced} securities from ${r.source}` }));
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
    setStatus((s) => ({ ...s, DIAGNOSE: `Diagnosing ${tickers.join(", ")}…` }));
    setBetaDiag(null);
    try {
      const r = await fetchBetaDiagnostics(tickers);
      setBetaDiag(r);
      setStatus((s) => ({ ...s, DIAGNOSE: "" }));
    } catch (e) {
      setStatus((s) => ({ ...s, DIAGNOSE: String(e) }));
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

  // Badge data for the family card. Only meaningful on the API path — in Electron the config
  // file is the source of truth and holds no env/stored distinction.
  const rowMeta: Record<string, { fromEnv?: boolean; hasPassword?: boolean }> = {};
  if (!isElectron) {
    for (const r of rows) {
      rowMeta[r.role] = { fromEnv: r.source === "env", hasPassword: r.has_password };
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* ── Header ── */}
      <header className="flex h-12 flex-shrink-0 items-center gap-3 border-b border-ocean-border bg-ocean-panel px-6">
        <h2 className="font-display text-sm font-semibold uppercase tracking-[0.12em] text-white">
          Settings
        </h2>
        <div className="ml-auto flex items-center gap-2">
          {savedNote && <span className="text-xs text-status-ok">✓ {savedNote}</span>}
          <button
            onClick={handleSaveAll}
            disabled={!isDirty || saving}
            aria-label="save-settings"
            className="rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white transition hover:bg-ocean-accent/80 disabled:opacity-40"
          >
            {saving
              ? "Saving…"
              : isElectron && dirtyRoles.size > 0
                ? `Save & restart backend (${dirtyCount})`
                : isDirty
                  ? `Save (${dirtyCount})`
                  : "Save"}
          </button>
          {onClose && (
            <button
              onClick={handleClose}
              className="rounded px-3 py-1.5 text-sm text-ocean-muted transition hover:bg-white/10 hover:text-white"
            >
              ✕ Close
            </button>
          )}
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* ── Nav rail — same recipe as the portfolios sidebar ── */}
        <aside className="flex w-56 flex-shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-ocean-border bg-ocean-panel/40 px-2 py-3">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              onClick={() => setSection(s.id)}
              aria-current={section === s.id ? "page" : undefined}
              className={`block w-full truncate rounded border-l-2 px-3 py-2 text-left text-sm transition ${
                section === s.id
                  ? "border-ocean-accent bg-ocean-accent/15 text-blue-300"
                  : "border-transparent text-slate-200 hover:bg-white/5"
              }`}
            >
              {s.label}
            </button>
          ))}
        </aside>

        {/* ── Section content ── */}
        <div className="min-w-0 flex-1 overflow-auto px-6 py-6">
          <div className="space-y-6">
            {error && (
              <div className="rounded border border-status-breach/40 bg-status-breach/10 p-3 text-sm text-status-breach">
                {error}
              </div>
            )}

            {/* ═══ GENERAL ═══ */}
            {section === "general" && (
              <>
                {/* Test Mode — surfaces only in the Electron shell, and only when relevant:
                    either no database is reachable (offer it) or it's already active (offer a
                    way out). It relaunches the backend on a throwaway SQLite DB with a seeded
                    demo book + synthetic market data. */}
                {canTestMode && inTestMode ? (
                  <div className="flex items-center justify-between gap-4 rounded-lg border border-status-watch/40 bg-status-watch/10 p-4">
                    <div>
                      <p className="font-display text-sm font-semibold text-status-watch">
                        Test Mode is active
                      </p>
                      <p className="mt-1 text-xs text-slate-300">
                        Running on a throwaway local database with synthetic demo data — nothing
                        here is real.
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
                      <p className="font-display text-sm font-semibold text-white">
                        No database connection found
                      </p>
                      <p className="mt-1 text-xs text-slate-300">
                        Can&apos;t reach a database, so risk and positions won&apos;t load. Explore
                        the app in Test Mode — a throwaway local database seeded with a demo book
                        and synthetic data.
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

                {/* Server price refresh — how often the backend pulls fresh marks for the open
                    books. "Live" streaming arrives with the Bloomberg feed (disabled until then). */}
                {onChangeMins && (
                  <>
                    <SectionLabel>Pricing</SectionLabel>
                    <Card>
                      <div className="mb-1">
                        <CardTitle>Price refresh</CardTitle>
                      </div>
                      <p className="mb-3 text-xs text-ocean-muted">
                        How often the server re-prices the open books. Manual refresh re-prices the
                        selected book immediately.
                      </p>
                      <div className="flex flex-wrap items-center gap-3 text-sm">
                        <select
                          value={String(pendingMins ?? refreshMins ?? 0)}
                          onChange={(e) => setPendingMins(Number(e.target.value))}
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
                    </Card>
                  </>
                )}
              </>
            )}

            {/* ═══ DATABASES ═══ */}
            {section === "databases" && (
              <>
                <p className="text-sm text-ocean-muted">
                  Point Neptune at the right database instances, grouped by the program that owns
                  them. A saved connection (host and port included) overrides the matching
                  environment variable. The password is write-only: leave it blank to keep the
                  stored secret.
                </p>

                {DB_FAMILIES.map((family) => (
                  <DbFamilyCard
                    key={family.id}
                    family={family}
                    conns={conns}
                    overrides={ownServer}
                    onChangeShared={(patch) => updateShared(family, patch)}
                    onChangeMember={updateMember}
                    onToggleOverride={(role, own) => toggleOverride(family, role, own)}
                    onTest={handleTest}
                    status={status}
                    rowMeta={rowMeta}
                    showSslmode={!isElectron}
                    testDisabledFor={(role) =>
                      !isElectron && dirtyRoles.has(role)
                        ? "Save first — this tests the stored connection, not the edits above."
                        : undefined
                    }
                  />
                ))}
              </>
            )}

            {/* ═══ API KEYS ═══ */}
            {section === "keys" && (
              <>
                <SectionLabel>Data providers</SectionLabel>
                <Card>
                  <div className="mb-1">
                    <CardTitle>Data provider API keys</CardTitle>
                  </div>
                  <p className="mb-3 text-xs text-ocean-muted">
                    Keys for external data feeds. FRED powers the macro database (rates, credit,
                    economic data); the same free key serves ALFRED (point-in-time vintages). Get
                    one at{" "}
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
                        {keyStatus[c.provider] && (
                          <span className="pb-2 text-sm text-ocean-muted">
                            {keyStatus[c.provider]}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </Card>
              </>
            )}

            {/* ═══ PORTFOLIOS ═══ */}
            {section === "portfolios" && <PortfoliosPanel onChanged={onPortfoliosChanged} />}

            {/* ═══ DATA INGEST ═══ */}
            {section === "ingest" && (
              <>
                <p className="text-sm text-ocean-muted">
                  Pull external data into Neptune's databases. These are one-shot operations, not
                  saved settings — each runs immediately and reports what it wrote.
                </p>

                <SectionLabel>Market data</SectionLabel>
                <Card>
                  <div className="mb-1">
                    <CardTitle>Prices &amp; factors</CardTitle>
                  </div>
                  <p className="mb-3 text-xs text-ocean-muted">
                    Backfills OHLCV history into the securities database and the Ken French daily
                    factor panel. The benchmark is always included — it defines the date index every
                    beta regression runs on.
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    <label className="flex items-center gap-1 text-xs text-ocean-muted">
                      <input
                        type="number"
                        min={1}
                        max={25}
                        value={years}
                        aria-label="backfill-years"
                        onChange={(e) =>
                          setYears(Math.max(1, Math.min(25, Number(e.target.value))))
                        }
                        className="np-input w-16"
                      />
                      yrs
                    </label>
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
                      value={ingestTicker}
                      onChange={(e) => setIngestTicker(e.target.value.toUpperCase())}
                      placeholder="WEN"
                      aria-label="backfill-one-ticker"
                      className="np-input w-24"
                    />
                    <button
                      onClick={() => handleIngest(parseTickers(ingestTicker))}
                      disabled={!ingestTicker.trim()}
                      className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200 disabled:opacity-50"
                    >
                      Backfill one
                    </button>
                    {status.SECURITIES && (
                      <span className="text-sm text-ocean-muted">{status.SECURITIES}</span>
                    )}
                  </div>
                </Card>

                <SectionLabel>Universe</SectionLabel>
                <Card>
                  <div className="mb-1">
                    <CardTitle>Universe projection</CardTitle>
                  </div>
                  <p className="mb-3 text-xs text-ocean-muted">
                    Re-projects the tradeable universe from CATO's securities master. Falls back to
                    a synthetic universe when no CATO connection is configured.
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      onClick={handleSync}
                      className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200"
                    >
                      Sync universe
                    </button>
                    {status.UNIVERSE && (
                      <span className="text-sm text-ocean-muted">{status.UNIVERSE}</span>
                    )}
                  </div>
                </Card>

                <SectionLabel>Macro</SectionLabel>
                <Card>
                  <div className="mb-1">
                    <CardTitle>Macro series</CardTitle>
                  </div>
                  <p className="mb-3 text-xs text-ocean-muted">
                    Pulls the registered macro catalog since 2000 — FRED for market/rates/credit,
                    ALFRED for point-in-time economic vintages. Needs a FRED key (API Keys).
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      onClick={handleMacroIngest}
                      className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200"
                    >
                      Backfill macro (FRED/ALFRED, since 2000)
                    </button>
                    {status.MACRO && (
                      <span className="text-sm text-ocean-muted">{status.MACRO}</span>
                    )}
                  </div>
                </Card>

                {catalog.length > 0 && (
                  <Card>
                    <div className="mb-1">
                      <CardTitle>Macro series catalog ({catalog.length})</CardTitle>
                    </div>
                    <p className="mb-3 text-xs text-ocean-muted">
                      Every series the macro backfill can pull. &quot;Loaded&quot; shows what&apos;s
                      already in your macro database.
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
                              <td className="px-3 py-1.5 font-mono text-slate-200">
                                {s.series_id}
                              </td>
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
                  </Card>
                )}
              </>
            )}

            {/* ═══ DIAGNOSTICS ═══ */}
            {section === "diagnostics" && (
              <>
                <SectionLabel>Coverage</SectionLabel>
                <DataHealth />

                <SectionLabel>Beta</SectionLabel>
                <Card>
                  <div className="mb-1">
                    <CardTitle>Diagnose beta</CardTitle>
                  </div>
                  <p className="mb-3 text-xs text-ocean-muted">
                    Shows the regression inputs behind a name&apos;s beta — stored bars, span
                    against the benchmark, observations used, and gap days — so a surprising number
                    can be traced to data rather than a genuine fit.
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      value={diagTicker}
                      onChange={(e) => setDiagTicker(e.target.value.toUpperCase())}
                      placeholder="WEN"
                      aria-label="diagnose-ticker"
                      className="np-input w-24"
                    />
                    <button
                      onClick={() => handleDiagnose(parseTickers(diagTicker))}
                      disabled={!diagTicker.trim()}
                      className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200 disabled:opacity-50"
                    >
                      Diagnose beta
                    </button>
                    {status.DIAGNOSE && (
                      <span className="text-sm text-ocean-muted">{status.DIAGNOSE}</span>
                    )}
                  </div>
                </Card>

                {betaDiag && <BetaDiagPanel diag={betaDiag} />}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function parseTickers(s: string): string[] {
  return s
    .split(/[,\s]+/)
    .map((t) => t.trim().toUpperCase())
    .filter(Boolean);
}

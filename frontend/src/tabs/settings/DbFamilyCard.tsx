import { useState } from "react";
import { Card, CardTitle, Field } from "./primitives";
import { EMPTY_CONN } from "./families";
import type { DbConn, DbFamily, DbRole } from "./families";

/** Does this member sit on a different server than the family's baseline? Only the server
 *  identity counts — the database name is per-member by definition, and the password is
 *  write-only so it is never a divergence signal. */
export function divergesFrom(base: DbConn, conn: DbConn | undefined): boolean {
  if (!conn) return false;
  return (
    conn.host !== base.host ||
    Number(conn.port) !== Number(base.port) ||
    conn.username !== base.username ||
    conn.sslmode !== base.sslmode
  );
}

/** The family's baseline server is its first member's — every other member either matches it
 *  (shared) or carries its own override. */
export function familyBase(family: DbFamily, conns: Record<string, DbConn>): DbConn {
  return conns[family.members[0].role] ?? EMPTY_CONN;
}

function ServerFields({
  value,
  onChange,
  hasStoredPassword,
  showSslmode,
  idPrefix,
}: {
  value: DbConn;
  onChange: (patch: Partial<DbConn>) => void;
  hasStoredPassword?: boolean;
  showSslmode?: boolean;
  idPrefix: string;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Field label="Host">
        <input
          className="np-input"
          aria-label={`${idPrefix}-host`}
          value={value.host}
          onChange={(e) => onChange({ host: e.target.value })}
        />
      </Field>
      <Field label="Port">
        <input
          className="np-input"
          type="number"
          aria-label={`${idPrefix}-port`}
          value={value.port}
          onChange={(e) => onChange({ port: Number(e.target.value) })}
        />
      </Field>
      <Field label="Username">
        <input
          className="np-input"
          aria-label={`${idPrefix}-username`}
          value={value.username}
          onChange={(e) => onChange({ username: e.target.value })}
        />
      </Field>
      <Field label={hasStoredPassword ? "Password (stored)" : "Password"}>
        <input
          className="np-input"
          type="password"
          aria-label={`${idPrefix}-password`}
          placeholder={hasStoredPassword ? "•••••• (unchanged)" : ""}
          value={value.password}
          onChange={(e) => onChange({ password: e.target.value })}
        />
      </Field>
      {showSslmode && (
        <Field label="SSL mode">
          <input
            className="np-input"
            aria-label={`${idPrefix}-sslmode`}
            placeholder="(optional)"
            value={value.sslmode}
            onChange={(e) => onChange({ sslmode: e.target.value })}
          />
        </Field>
      )}
    </div>
  );
}

/** One database family: a shared server plus the databases on it.
 *
 *  The shared block edits every member that has not been broken out; a broken-out member gets
 *  its own server fields under Advanced. This mirrors how the databases are actually
 *  provisioned — one server per program — while still allowing the real case where one of them
 *  lives elsewhere (CATO's master is commonly on its own instance). */
export function DbFamilyCard({
  family,
  conns,
  overrides,
  onChangeShared,
  onChangeMember,
  onToggleOverride,
  onTest,
  status,
  rowMeta,
  showSslmode,
  testDisabledFor,
}: {
  family: DbFamily;
  conns: Record<string, DbConn>;
  /** Members broken out onto their own server. Explicit rather than derived from the values,
   *  so checking the box holds even before the host is edited away from the shared one. */
  overrides: Set<string>;
  /** Applies a patch to every member that shares the family server. */
  onChangeShared: (patch: Partial<DbConn>) => void;
  onChangeMember: (role: DbRole, patch: Partial<DbConn>) => void;
  onToggleOverride: (role: DbRole, own: boolean) => void;
  onTest: (role: DbRole) => void;
  status: Record<string, string>;
  /** Per-role badge info from the API rows (absent on the Electron path). */
  rowMeta?: Record<string, { fromEnv?: boolean; hasPassword?: boolean }>;
  showSslmode?: boolean;
  testDisabledFor: (role: DbRole) => string | undefined;
}) {
  const base = familyBase(family, conns);
  const overrideCount = family.members.filter((m) => overrides.has(m.role)).length;
  // Divergence that already exists in the stored config is the reason to show Advanced
  // unprompted — otherwise a member quietly pointing somewhere else would be invisible.
  const [showAdvanced, setShowAdvanced] = useState(overrideCount > 0);
  const advancedOpen = showAdvanced || overrideCount > 0;

  const meta = (role: string) => rowMeta?.[role] ?? {};

  return (
    <Card>
      <div className="mb-1">
        <CardTitle>{family.label}</CardTitle>
      </div>
      <p className="mb-4 text-xs text-ocean-muted">{family.blurb}</p>

      <div className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-slate-400">
        Server
      </div>
      <ServerFields
        value={base}
        onChange={onChangeShared}
        hasStoredPassword={meta(family.members[0].role).hasPassword}
        showSslmode={showSslmode}
        idPrefix={`${family.id}-shared`}
      />

      <div className="mb-2 mt-5 text-xs font-semibold uppercase tracking-[0.08em] text-slate-400">
        Databases
      </div>
      <div className="space-y-2">
        {family.members.map((m) => {
          const c = conns[m.role] ?? EMPTY_CONN;
          const own = overrides.has(m.role);
          return (
            <div key={m.role} className="flex flex-wrap items-center gap-3">
              <div className="w-64 shrink-0">
                <div className="truncate text-sm text-slate-200">{m.label}</div>
                <div className="mt-0.5 flex flex-wrap items-center gap-1">
                  {m.bootstrap && (
                    <span className="rounded bg-ocean-accent/20 px-1.5 py-0.5 text-[10px] text-ocean-accent">
                      bootstrap · applies on restart
                    </span>
                  )}
                  {meta(m.role).fromEnv && (
                    <span className="rounded bg-ocean-border/40 px-1.5 py-0.5 text-[10px] text-ocean-muted">
                      from .env
                    </span>
                  )}
                  {own && (
                    <span className="rounded bg-status-watch/15 px-1.5 py-0.5 text-[10px] text-status-watch">
                      own server
                    </span>
                  )}
                </div>
              </div>
              <input
                className="np-input w-56"
                aria-label={`${m.role.toLowerCase()}-database`}
                placeholder={m.defaultDatabase}
                value={c.database}
                onChange={(e) => onChangeMember(m.role, { database: e.target.value })}
              />
              <button
                onClick={() => onTest(m.role)}
                aria-label={`${m.role.toLowerCase()}-test`}
                disabled={Boolean(testDisabledFor(m.role))}
                title={testDisabledFor(m.role)}
                className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200 disabled:opacity-50"
              >
                Test
              </button>
              {status[m.role] && (
                <span className="text-xs text-ocean-muted">{status[m.role]}</span>
              )}
            </div>
          );
        })}
      </div>

      {family.members.length > 1 && (
        <div className="mt-4">
          <button
            onClick={() => setShowAdvanced((v) => !v)}
            className="rounded border border-ocean-border px-3 py-1.5 text-xs text-ocean-muted hover:text-slate-200"
          >
            {advancedOpen ? "▾" : "▸"} Advanced — per-database overrides
            {overrideCount > 0 && (
              <span className="ml-2 text-status-watch">
                {overrideCount} on {overrideCount === 1 ? "its" : "their"} own server
              </span>
            )}
          </button>

          {advancedOpen && (
            <div className="mt-3 space-y-4 rounded border border-ocean-border/60 bg-ocean-bg/40 p-4">
              <p className="text-xs text-ocean-muted">
                Break a database out onto its own server. Left unchecked it follows the family
                server above.
              </p>
              {family.members.slice(1).map((m) => {
                const own = overrides.has(m.role);
                return (
                  <div key={m.role}>
                    <label className="flex items-center gap-2 text-sm text-slate-200">
                      <input
                        type="checkbox"
                        aria-label={`${m.role.toLowerCase()}-own-server`}
                        checked={own}
                        onChange={(e) => onToggleOverride(m.role, e.target.checked)}
                      />
                      {m.label} uses a different server
                    </label>
                    {own && (
                      <div className="mt-2 pl-6">
                        <ServerFields
                          value={conns[m.role] ?? EMPTY_CONN}
                          onChange={(patch) => onChangeMember(m.role, patch)}
                          hasStoredPassword={meta(m.role).hasPassword}
                          showSslmode={showSslmode}
                          idPrefix={m.role.toLowerCase()}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

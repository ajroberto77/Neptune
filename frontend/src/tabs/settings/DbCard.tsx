import { Card, CardTitle, Field } from "./primitives";
import type { DbConn, DbMember } from "./families";

/** The six connection inputs, controlled. Shared by the per-database card here and (later) by
 *  the family card's shared-server block, so the two can never drift apart. */
export function DbConnFields({
  value,
  onChange,
  hasStoredPassword,
  showSslmode,
  showDatabase = true,
  idPrefix,
}: {
  value: DbConn;
  onChange: (patch: Partial<DbConn>) => void;
  hasStoredPassword?: boolean;
  /** Electron's config has no sslmode field — only the API path does. */
  showSslmode?: boolean;
  showDatabase?: boolean;
  /** Namespaces the aria-labels so multiple field groups on one page stay addressable. */
  idPrefix: string;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
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
      {showDatabase && (
        <Field label="Database">
          <input
            className="np-input"
            aria-label={`${idPrefix}-database`}
            value={value.database}
            onChange={(e) => onChange({ database: e.target.value })}
          />
        </Field>
      )}
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

/** One database's connection card. Replaces the two near-identical Electron and API blocks
 *  that used to be copy-pasted in Settings.tsx — the caller supplies the handlers for whichever
 *  path is live, and everything else is identical. */
export function DbCard({
  member,
  value,
  onChange,
  onSave,
  onTest,
  status,
  saveLabel,
  fromEnv,
  hasStoredPassword,
  showSslmode,
  testDisabledReason,
}: {
  member: DbMember;
  value: DbConn;
  onChange: (patch: Partial<DbConn>) => void;
  onSave: () => void;
  onTest: () => void;
  status?: string;
  saveLabel: string;
  fromEnv?: boolean;
  hasStoredPassword?: boolean;
  showSslmode?: boolean;
  /** Set to disable Test with an explanation — the API path tests the *saved* URL, so testing
   *  while the form is dirty would report on values the user can no longer see. */
  testDisabledReason?: string;
}) {
  return (
    <Card>
      <div className="mb-3 flex items-center justify-between gap-3">
        <CardTitle>{member.label}</CardTitle>
        <div className="flex items-center gap-2">
          {fromEnv && (
            <span className="rounded bg-ocean-border/40 px-2 py-0.5 text-xs text-ocean-muted">
              from .env
            </span>
          )}
          {member.bootstrap && (
            <span className="rounded bg-ocean-accent/20 px-2 py-0.5 text-xs text-ocean-accent">
              bootstrap · applies on restart
            </span>
          )}
        </div>
      </div>

      <DbConnFields
        value={value}
        onChange={onChange}
        hasStoredPassword={hasStoredPassword}
        showSslmode={showSslmode}
        idPrefix={member.role.toLowerCase()}
      />

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          onClick={onSave}
          aria-label={`${member.role.toLowerCase()}-save`}
          className="rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-ocean-accent/80"
        >
          {saveLabel}
        </button>
        <button
          onClick={onTest}
          aria-label={`${member.role.toLowerCase()}-test`}
          disabled={Boolean(testDisabledReason)}
          title={testDisabledReason}
          className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted hover:text-slate-200 disabled:opacity-50"
        >
          Test connection
        </button>
        {status && <span className="text-sm text-ocean-muted">{status}</span>}
      </div>
    </Card>
  );
}

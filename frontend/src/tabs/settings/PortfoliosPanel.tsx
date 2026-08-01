import { useEffect, useState } from "react";
import type { EntityRow, FirmRow, PersonRow, PortfolioMeta } from "../../api/client";
import {
  createPortfolio,
  deletePortfolio,
  fetchEntities,
  fetchFirms,
  fetchPeople,
  fetchPortfolios,
} from "../../api/client";
import { Card, CardTitle, Field } from "./primitives";

/** Portfolios: add and remove books. A book carries full ownership — a management firm, the
 *  investor entity whose capital it runs, and its lead PM — picked from the org graph. Removing
 *  a book is refused by the server while it still holds open positions (flatten it first), and
 *  is bookkeeping only — it never routes or unwinds anything at a venue. */
export function PortfoliosPanel({ onChanged }: { onChanged?: () => void }) {
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
    <Card>
      <div className="mb-1">
        <CardTitle>Portfolios</CardTitle>
      </div>
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
                <tr key={b.id} className="border-t border-ocean-border/70">
                  <td className="px-3 py-2">
                    <div className="text-slate-200">{b.name}</div>
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
    </Card>
  );
}

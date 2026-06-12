// PortfolioSidebar — a left rail on the Portfolio tab listing the selectable books. The
// Consolidated roll-up is pinned at the top; real books sit (indented) under their mandate
// roll-up (Long / Short, Long Only). Clicking a row selects that view; selection persists
// across the other tabs. Replaces the old <select> dropdown.

import type { PortfolioMeta } from "../api/client";

interface Props {
  longShortBooks: PortfolioMeta[];
  longOnlyBooks: PortfolioMeta[];
  selectedId: string;
  onSelect: (id: string) => void;
  consolidatedId: string;
  longShortGroupId: string;
  longOnlyGroupId: string;
}

export function PortfolioSidebar({
  longShortBooks,
  longOnlyBooks,
  selectedId,
  onSelect,
  consolidatedId,
  longShortGroupId,
  longOnlyGroupId,
}: Props) {
  // A single row. `rollup` rows (Consolidated + the two group views) are bold; books are indented.
  const row = (id: string, label: string, opts?: { indent?: boolean; rollup?: boolean }) => {
    const active = id === selectedId;
    return (
      <button
        key={id}
        onClick={() => onSelect(id)}
        aria-current={active ? "true" : undefined}
        className={[
          "block w-full truncate rounded px-3 py-2 text-left text-sm transition",
          opts?.indent ? "pl-6" : "",
          opts?.rollup ? "font-semibold" : "font-normal",
          active
            ? "bg-ocean-accent/15 text-ocean-accent"
            : "text-slate-200 hover:bg-white/5",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        {label}
      </button>
    );
  };

  return (
    <aside className="w-60 flex-shrink-0 self-start space-y-0.5 border-r border-ocean-border pr-3">
      <p className="px-3 pb-1 pt-1 font-display text-[10px] uppercase tracking-[0.14em] text-ocean-muted">
        Portfolios
      </p>

      {row(consolidatedId, "Consolidated Positions", { rollup: true })}

      {longShortBooks.length > 0 && (
        <>
          {row(longShortGroupId, "Long / Short", { rollup: true })}
          {longShortBooks.map((p) => row(p.id, p.name, { indent: true }))}
        </>
      )}

      {longOnlyBooks.length > 0 && (
        <>
          {row(longOnlyGroupId, "Long Only", { rollup: true })}
          {longOnlyBooks.map((p) => row(p.id, p.name, { indent: true }))}
        </>
      )}
    </aside>
  );
}

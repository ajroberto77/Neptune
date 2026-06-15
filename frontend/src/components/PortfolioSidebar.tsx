// PortfolioSidebar — the persistent left rail listing the selectable portfolio views. Three
// visual tiers so the hierarchy reads at a glance: the Consolidated master roll-up (brightest,
// pinned at top), the Long / Short and Long Only mandate groups (quieter uppercase section
// labels), and the individual books (normal rows). All three are selectable and share one
// selection; the active state is a uniform accent overlay layered on top of the resting tier
// styles, so "what's selected" and "which tier it is" never collide.

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

type Tier = "consolidated" | "group" | "book";

export function PortfolioSidebar({
  longShortBooks,
  longOnlyBooks,
  selectedId,
  onSelect,
  consolidatedId,
  longShortGroupId,
  longOnlyGroupId,
}: Props) {
  const row = (id: string, label: string, tier: Tier) => {
    const active = id === selectedId;
    // Resting styles carry the hierarchy; books are NOT indented — the format (case/size/weight)
    // is the differentiator, not an offset.
    const resting =
      tier === "consolidated"
        ? "text-sm font-semibold"
        : tier === "group"
          ? "mt-2 font-display text-[11px] font-semibold uppercase tracking-[0.12em]"
          : "text-sm font-normal";
    // Resting text colour per tier (kept separate from the active colour to avoid Tailwind clashes).
    const restColor =
      tier === "consolidated"
        ? "text-slate-100"
        : tier === "group"
          ? "text-slate-400"
          : "text-slate-200";
    return (
      <button
        key={id}
        onClick={() => onSelect(id)}
        aria-current={active ? "true" : undefined}
        className={[
          "block w-full truncate rounded border-l-2 px-3 py-2 text-left transition",
          resting,
          active
            ? "border-ocean-accent bg-ocean-accent/15 text-blue-300"
            : `border-transparent ${restColor} hover:bg-white/5`,
        ].join(" ")}
      >
        {label}
      </button>
    );
  };

  return (
    <aside className="flex w-56 flex-shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-ocean-border bg-ocean-panel/40 px-2 py-3">
      <p className="px-3 pb-1 pt-1 font-display text-[10px] uppercase tracking-[0.16em] text-slate-400">
        Portfolio
      </p>

      {row(consolidatedId, "Consolidated Positions", "consolidated")}
      <div className="mx-3 my-1 border-t border-ocean-border" />

      {longShortBooks.length > 0 && (
        <>
          {row(longShortGroupId, "Long / Short", "group")}
          {longShortBooks.map((p) => row(p.id, p.name, "book"))}
        </>
      )}

      {longOnlyBooks.length > 0 && (
        <>
          {row(longOnlyGroupId, "Long Only", "group")}
          {longOnlyBooks.map((p) => row(p.id, p.name, "book"))}
        </>
      )}
    </aside>
  );
}

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
  /** Return a reason to render a row disabled (e.g. not hedgeable on the Hedge tab); falsy = live. */
  disabledReason?: (id: string) => string | undefined;
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
  disabledReason,
}: Props) {
  const row = (id: string, label: string, tier: Tier) => {
    const active = id === selectedId;
    const reason = disabledReason?.(id);
    const disabled = Boolean(reason);
    // Group headers can be in the disabled set too (e.g. Hedge disables the roll-up groups —
    // you can't target "Long / Short" itself as a hedge book), but that's about click behavior,
    // not legibility: a section label reading "unavailable" is misleading, since the label
    // itself was never a hedge target — only the books under it are. So the dimmed disabled
    // *color* only applies to book/consolidated rows; group labels keep their own tier color
    // and stay merely non-clickable (cursor + title tooltip) when disabled.
    const dimDisabled = disabled && tier !== "group";
    // Resting styles carry the hierarchy; books are NOT indented — the format (case/size/weight)
    // is the differentiator, not an offset.
    const resting =
      tier === "consolidated"
        ? "text-sm font-semibold"
        : tier === "group"
          ? "mt-2 font-display text-xs font-semibold uppercase tracking-[0.08em]"
          : "text-sm font-normal";
    // Resting text colour per tier (kept separate from the active colour to avoid Tailwind clashes).
    // Group labels are structural nav headers, not decorative — text-slate-400 (used elsewhere for
    // secondary/muted text) reads AA-legal in isolation but disappears next to uppercase+tracking at
    // this size, so it gets a step brighter than the ordinary "muted" tier.
    const restColor = dimDisabled
      ? "text-slate-600"
      : tier === "consolidated"
        ? "text-slate-100"
        : tier === "group"
          ? "text-slate-300"
          : "text-slate-200";
    return (
      <button
        key={id}
        onClick={disabled ? undefined : () => onSelect(id)}
        disabled={disabled}
        aria-disabled={disabled || undefined}
        aria-current={active ? "true" : undefined}
        title={reason}
        className={[
          "block w-full truncate rounded border-l-2 px-3 py-2 text-left transition",
          resting,
          disabled
            ? `border-transparent ${restColor} cursor-not-allowed${dimDisabled ? " opacity-50" : ""}`
            : active
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

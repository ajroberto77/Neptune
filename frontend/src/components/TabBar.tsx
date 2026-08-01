// TabBar — the app's primary navigation as a horizontal row of top tabs (the major views),
// matching the Iridium suite. Uppercase, letter-spaced tabs with an active underline and a
// pulsing "running" dot for tabs with a background job in flight. The persistent left sidebar
// (portfolios) is a separate, always-on column — see App.

interface Props {
  tabs: readonly string[];
  active: string;
  onChange: (tab: string) => void;
  /** Tabs with a background job in flight get a pulsing dot. */
  running?: Set<string>;
}

export function TabBar({ tabs, active, onChange, running }: Props) {
  return (
    <div className="app-drag flex flex-shrink-0 items-center overflow-x-auto border-b border-ocean-border bg-ocean-bg">
      {tabs.map((tab) => {
        const isActive = tab === active;
        return (
          <button
            key={tab}
            onClick={() => onChange(tab)}
            aria-current={isActive ? "page" : undefined}
            className={`app-no-drag flex h-9 flex-shrink-0 items-center gap-1.5 border-b-2 px-4 text-[11px] font-bold uppercase tracking-[0.12em] transition ${
              isActive
                ? "border-ocean-accent text-ocean-accent"
                : "border-transparent text-ocean-muted hover:text-slate-200"
            }`}
          >
            {tab}
            {running?.has(tab) && (
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-status-watch" />
            )}
          </button>
        );
      })}
      <div className="flex-1" />
    </div>
  );
}

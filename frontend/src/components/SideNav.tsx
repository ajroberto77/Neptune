// SideNav — the app's primary navigation as a persistent left column (flush to the far left,
// full height), matching the Iridium suite (CATO/Mercury) layout. Replaces the old horizontal
// top tab bar. Uppercase items with an active highlight + accent left-border, and a pulsing
// "running" dot for tabs with a background job in flight.

interface Props {
  tabs: readonly string[];
  active: string;
  onChange: (tab: string) => void;
  /** Tabs with a background job in flight get a pulsing dot. */
  running?: Set<string>;
}

export function SideNav({ tabs, active, onChange, running }: Props) {
  return (
    <nav className="flex w-44 flex-shrink-0 flex-col overflow-y-auto border-r border-ocean-border bg-ocean-panel/40 py-3">
      <p className="px-4 pb-2 font-display text-[10px] uppercase tracking-[0.16em] text-ocean-muted">
        Views
      </p>
      {tabs.map((tab) => {
        const isActive = tab === active;
        return (
          <button
            key={tab}
            onClick={() => onChange(tab)}
            aria-current={isActive ? "page" : undefined}
            className={`flex items-center justify-between gap-2 border-l-2 px-4 py-2 text-left text-[13px] font-medium uppercase tracking-wide transition ${
              isActive
                ? "border-ocean-accent bg-ocean-accent/10 text-ocean-accent"
                : "border-transparent text-ocean-muted hover:bg-white/5 hover:text-slate-200"
            }`}
          >
            <span>{tab}</span>
            {running?.has(tab) && (
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-status-watch" />
            )}
          </button>
        );
      })}
    </nav>
  );
}

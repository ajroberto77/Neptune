import { useState } from "react";
import type { ColumnKey } from "./blotterColumns";
import { ALL_COLUMNS } from "./blotterColumns";

/** A small icon button opening a plain checklist to show/hide and reorder Blotter columns.
 *  No portal/menu library (none exists in this frontend) — a conditionally-rendered,
 *  absolutely-positioned panel closes the same way DataHealth's own controls behave. Fully
 *  controlled: the parent owns `columns` and persistence; this component only proposes the
 *  next array via `onChange`. */
export function BlotterColumnPicker({
  columns,
  onChange,
}: {
  columns: ColumnKey[];
  onChange: (columns: ColumnKey[]) => void;
}) {
  const [open, setOpen] = useState(false);

  function toggle(key: ColumnKey) {
    if (columns.includes(key)) {
      onChange(columns.filter((k) => k !== key));
    } else {
      // Re-add at its canonical position (ALL_COLUMNS order) among currently-visible columns,
      // rather than always appending at the end.
      const canonicalOrder = ALL_COLUMNS.map((c) => c.key);
      const next = canonicalOrder.filter((k) => columns.includes(k) || k === key);
      onChange(next);
    }
  }

  function move(index: number, delta: -1 | 1) {
    const target = index + delta;
    if (target < 0 || target >= columns.length) return;
    const next = [...columns];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="Customize columns"
        className="rounded border border-ocean-border px-2 py-1 text-xs text-ocean-muted hover:text-slate-200"
      >
        Columns
      </button>
      {open && (
        <>
          {/* Click-outside catcher */}
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-20 mt-1 w-56 rounded-lg border border-ocean-border bg-ocean-panel p-2 shadow-lg">
            {columns.map((key, i) => {
              const label = ALL_COLUMNS.find((c) => c.key === key)?.label ?? key;
              return (
                <div key={key} className="flex items-center gap-2 py-1 text-xs">
                  <input
                    type="checkbox"
                    checked
                    aria-label={`Show ${label}`}
                    onChange={() => toggle(key)}
                  />
                  <span className="flex-1 text-slate-200">{label}</span>
                  <button
                    aria-label={`Move ${label} up`}
                    disabled={i === 0}
                    onClick={() => move(i, -1)}
                    className="px-1 text-ocean-muted hover:text-slate-200 disabled:opacity-30"
                  >
                    ↑
                  </button>
                  <button
                    aria-label={`Move ${label} down`}
                    disabled={i === columns.length - 1}
                    onClick={() => move(i, 1)}
                    className="px-1 text-ocean-muted hover:text-slate-200 disabled:opacity-30"
                  >
                    ↓
                  </button>
                </div>
              );
            })}
            {ALL_COLUMNS.filter((c) => !columns.includes(c.key)).length > 0 && (
              <div className="mt-1 border-t border-ocean-border/70 pt-1">
                {ALL_COLUMNS.filter((c) => !columns.includes(c.key)).map((c) => (
                  <div key={c.key} className="flex items-center gap-2 py-1 text-xs">
                    <input
                      type="checkbox"
                      checked={false}
                      aria-label={`Show ${c.label}`}
                      onChange={() => toggle(c.key)}
                    />
                    <span className="flex-1 text-ocean-faint">{c.label}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

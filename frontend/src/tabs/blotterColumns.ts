/** The Blotter's customizable column set: which of the fixed 8 columns show, and in what
 *  order. Persisted as a UI preference in localStorage (not app data) -- the first
 *  localStorage usage in this frontend, kept deliberately minimal/isolated rather than a
 *  generic settings-persistence abstraction speculatively built for a second use case. */

export type ColumnKey =
  | "date"
  | "ticker"
  | "action"
  | "shares"
  | "price"
  | "effect"
  | "realized_pnl"
  | "origin";

export const ALL_COLUMNS: { key: ColumnKey; label: string }[] = [
  { key: "date", label: "Date" },
  { key: "ticker", label: "Ticker" },
  { key: "action", label: "Action" },
  { key: "shares", label: "Shares" },
  { key: "price", label: "Price" },
  { key: "effect", label: "Effect" },
  { key: "realized_pnl", label: "Realized P&L" },
  { key: "origin", label: "Origin" },
];

// All 8, in the original hardcoded order — first-run behavior is byte-identical to before
// the picker existed.
export const DEFAULT_VISIBLE: ColumnKey[] = ALL_COLUMNS.map((c) => c.key);

// Versioned so a future column-set change (rename/remove a key) can invalidate cleanly
// instead of silently misreading an old blob.
const STORAGE_KEY = "neptune.blotter.columns.v1";

const VALID_KEYS = new Set<ColumnKey>(ALL_COLUMNS.map((c) => c.key));

export function loadBlotterColumns(): ColumnKey[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_VISIBLE;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return DEFAULT_VISIBLE;
    // Filter out any key no longer in ALL_COLUMNS (a stale blob from a future/past column
    // set) rather than rendering a blank/broken header for it.
    const filtered = parsed.filter((k): k is ColumnKey => VALID_KEYS.has(k));
    return filtered.length > 0 ? filtered : DEFAULT_VISIBLE;
  } catch {
    return DEFAULT_VISIBLE;
  }
}

export function saveBlotterColumns(columns: ColumnKey[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(columns));
  } catch {
    // localStorage unavailable (private browsing, quota, etc.) — the picker still works
    // for the session, it just won't persist across a reload.
  }
}

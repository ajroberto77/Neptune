// TestModeModal — shown when the backend can't reach a real database after a grace period.
// Offers to relaunch the backend in Test Mode: a throwaway local SQLite DB seeded with a demo
// book and synthetic market data, so the app is fully explorable with zero Postgres. No real
// database is ever touched.

interface Props {
  onUseTestMode: () => void;
  onDismiss: () => void;
  switching: boolean;
}

export function TestModeModal({ onUseTestMode, onDismiss, switching }: Props) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-md rounded-lg border border-ocean-border bg-ocean-panel p-6 shadow-xl">
        <h2 className="font-display text-lg font-semibold text-white">No database connection</h2>
        <p className="mt-2 text-sm leading-relaxed text-slate-300">
          Neptune can&apos;t reach its database, so risk and positions can&apos;t load. You can
          explore the app in{" "}
          <span className="font-semibold text-ocean-accent">Test Mode</span> — a throwaway local
          database seeded with a demo book and synthetic market data. Nothing real is touched, and
          you can switch back by restarting the app.
        </p>
        <div className="mt-5 flex justify-end gap-3">
          <button
            onClick={onDismiss}
            disabled={switching}
            className="rounded border border-ocean-border px-3 py-1.5 text-sm text-ocean-muted transition hover:text-slate-200 disabled:opacity-50"
          >
            Keep trying
          </button>
          <button
            onClick={onUseTestMode}
            disabled={switching}
            className="rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white transition hover:bg-ocean-accent/80 disabled:opacity-50"
          >
            {switching ? "Starting Test Mode…" : "Use Test Mode"}
          </button>
        </div>
      </div>
    </div>
  );
}

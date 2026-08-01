import type { ReactNode } from "react";

/** Rail-matching group label. Same recipe as PortfolioSidebar's section headers, so the
 *  Settings content column reads as the same app as the rail beside it. */
export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="font-display text-[10px] uppercase tracking-[0.16em] text-slate-400">
      {children}
    </div>
  );
}

/** The standard settings card. */
export function Card({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-ocean-border bg-ocean-panel p-5">{children}</div>
  );
}

export function CardTitle({ children }: { children: ReactNode }) {
  return (
    <h3 className="font-display text-sm uppercase tracking-wide text-ocean-muted">{children}</h3>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-ocean-muted">{label}</span>
      {children}
    </label>
  );
}

export function Metric({
  label,
  value,
  ok,
  hint,
}: {
  label: string;
  value: string | number;
  ok: boolean;
  hint?: string;
}) {
  return (
    <div>
      <div className="text-xs uppercase text-ocean-muted">{label}</div>
      <div className={`font-mono text-lg ${ok ? "text-status-ok" : "text-status-breach"}`}>
        {value}
      </div>
      {hint && <div className="text-[11px] text-ocean-muted/70">{hint}</div>}
    </div>
  );
}

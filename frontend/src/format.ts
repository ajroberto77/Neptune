export function money(value: number): string {
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

/** Currency with cents, e.g. "$204.13" — for per-share prices. */
export function price(value: number): string {
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** Signed currency, e.g. "+$1,234" / "-$567" — for P&L cells. */
export function signedMoney(value: number): string {
  const sign = value >= 0 ? "+" : "-";
  return sign + money(Math.abs(value));
}

/** Tailwind text color for a P&L value (green up, red down, muted flat). */
export function pnlColor(value: number): string {
  if (value > 0) return "text-status-ok";
  if (value < 0) return "text-status-breach";
  return "text-ocean-muted";
}

/** Signed percentage, e.g. "+1.24%" / "-0.50%", or "—" when not computable. */
export function signedPct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value >= 0 ? "+" : "-";
  return sign + Math.abs(value * 100).toFixed(2) + "%";
}

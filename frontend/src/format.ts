export function money(value: number): string {
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
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

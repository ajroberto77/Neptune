import type { Status } from "../types";

const STYLES: Record<Status, string> = {
  OK: "bg-status-ok/15 text-status-ok border-status-ok/40",
  WATCH: "bg-status-watch/15 text-status-watch border-status-watch/40",
  BREACH: "bg-status-breach/15 text-status-breach border-status-breach/40",
};

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span
      className={`inline-block rounded border px-2 py-0.5 text-xs font-mono font-medium ${STYLES[status]}`}
    >
      {status}
    </span>
  );
}

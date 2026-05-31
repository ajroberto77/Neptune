import type {
  Frontier,
  HedgeProposal,
  PositionRow,
  RiskSummary,
  StressReport,
} from "../types";

async function getJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export function fetchRisk(portfolioId: string): Promise<RiskSummary> {
  return getJSON<RiskSummary>(`/portfolios/${portfolioId}/risk`);
}

export function fetchPositions(portfolioId: string): Promise<PositionRow[]> {
  return getJSON<PositionRow[]>(`/portfolios/${portfolioId}/positions`);
}

export function proposeHedge(
  portfolioId: string,
  sectorLimit?: number,
): Promise<HedgeProposal> {
  const q = sectorLimit !== undefined ? `?sector_limit=${sectorLimit}` : "";
  return getJSON<HedgeProposal>(`/portfolios/${portfolioId}/hedge/propose${q}`, {
    method: "POST",
  });
}

export function fetchFrontier(portfolioId: string): Promise<Frontier> {
  return getJSON<Frontier>(`/portfolios/${portfolioId}/hedge/frontier`, {
    method: "POST",
  });
}

export function fetchStress(portfolioId: string): Promise<StressReport> {
  return getJSON<StressReport>(`/portfolios/${portfolioId}/stress`, {
    method: "POST",
  });
}

// --- Settings: configurable DB connections ---
import type { ConnectionRow, ConnectionInput, SyncResult } from "../types";

export function fetchConnections(): Promise<ConnectionRow[]> {
  return getJSON<ConnectionRow[]>(`/settings/connections`);
}

export function saveConnection(
  role: string,
  body: ConnectionInput,
): Promise<ConnectionRow> {
  return getJSON<ConnectionRow>(`/settings/connections/${role}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function testConnection(
  role: string,
): Promise<{ role: string; ok: boolean; error?: string }> {
  return getJSON(`/settings/connections/${role}/test`, { method: "POST" });
}

export function syncUniverse(): Promise<SyncResult> {
  return getJSON<SyncResult>(`/settings/universe/sync`, { method: "POST" });
}

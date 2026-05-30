import type { Frontier, HedgeProposal, PositionRow, RiskSummary } from "../types";

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

export function proposeHedge(portfolioId: string): Promise<HedgeProposal> {
  return getJSON<HedgeProposal>(`/portfolios/${portfolioId}/hedge/propose`, {
    method: "POST",
  });
}

export function fetchFrontier(portfolioId: string): Promise<Frontier> {
  return getJSON<Frontier>(`/portfolios/${portfolioId}/hedge/frontier`, {
    method: "POST",
  });
}

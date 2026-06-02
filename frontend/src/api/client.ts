import type {
  FactorMonitor,
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

export function fetchFactorMonitor(portfolioId: string): Promise<FactorMonitor> {
  return getJSON<FactorMonitor>(`/portfolios/${portfolioId}/factor-monitor`);
}

export function proposeHedge(
  portfolioId: string,
  sectorLimit?: number,
  maxNames?: number,
  betaAddBudget?: number,
): Promise<HedgeProposal> {
  const params = new URLSearchParams();
  if (sectorLimit !== undefined) params.set("sector_limit", String(sectorLimit));
  if (maxNames !== undefined) params.set("max_names", String(maxNames));
  if (betaAddBudget !== undefined) params.set("beta_add_budget", String(betaAddBudget));
  const q = params.toString() ? `?${params}` : "";
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

// --- Trade: record an executed transaction / close a position ---
import type { TransactionInput } from "../types";

export function recordTransaction(
  portfolioId: string,
  body: TransactionInput,
): Promise<{ id: number }> {
  return getJSON(`/portfolios/${portfolioId}/transactions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function closePosition(
  portfolioId: string,
  positionId: number,
  quantity: number,
  exitPrice: number,
): Promise<{ realized_pnl: number }> {
  return getJSON(`/portfolios/${portfolioId}/positions/${positionId}/reduce`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ quantity, exit_price: exitPrice }),
  });
}

export function refreshPrices(
  portfolioId: string,
): Promise<{ updated_bars: number; tickers: number; errors: string[] }> {
  return getJSON(`/portfolios/${portfolioId}/refresh-prices`, { method: "POST" });
}

export interface PortfolioMeta {
  id: string;
  name: string;
  mandate: "LONG_SHORT" | "LONG_ONLY";
}

export function fetchPortfolios(): Promise<PortfolioMeta[]> {
  return getJSON<PortfolioMeta[]>("/portfolios");
}

export function approveHedge(
  portfolioId: string,
  shorts: { ticker: string; shares: number; price: number }[],
): Promise<{ booked: number }> {
  return getJSON(`/portfolios/${portfolioId}/hedge/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ shorts }),
  });
}

export interface SecuritiesHealth {
  benchmark?: string;
  benchmark_bars?: number;
  securities_projected: number;
  names_with_30plus_bars: number;
  names_with_computable_beta?: number;
  factor_panel?: string;
  source: string;
  reason: string;
}

export function fetchSecuritiesHealth(): Promise<SecuritiesHealth> {
  return getJSON<SecuritiesHealth>("/securities/health");
}

export function getPriceRefresh(): Promise<{ minutes: number }> {
  return getJSON("/settings/price-refresh");
}

export function setPriceRefresh(minutes: number): Promise<{ minutes: number }> {
  return getJSON("/settings/price-refresh", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ minutes }),
  });
}

// --- Settings: configurable DB connections ---
import type {
  ConnectionRow,
  ConnectionInput,
  SyncResult,
  IngestResult,
  FactorIngestResult,
  BetaDiagnostics,
  BetaHistory,
  HedgeBacktest,
  HedgeCalibration,
} from "../types";

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

export function ingestPrices(tickers?: string[], years?: number): Promise<IngestResult> {
  // No tickers → backfill the whole projection; a list → just those names. `years` sets depth.
  const body: Record<string, unknown> = {};
  if (tickers && tickers.length) body.tickers = tickers;
  if (years) body.years = years;
  return getJSON<IngestResult>(`/securities/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function fetchBetaHistory(
  portfolioId: string,
  points = 26,
  step = 5,
): Promise<BetaHistory> {
  return getJSON<BetaHistory>(
    `/portfolios/${portfolioId}/beta-history?points=${points}&step=${step}`,
  );
}

export function fetchHedgeBacktest(
  portfolioId: string,
  points = 24,
  step = 21,
): Promise<HedgeBacktest> {
  return getJSON<HedgeBacktest>(
    `/portfolios/${portfolioId}/hedge-backtest?points=${points}&step=${step}`,
  );
}

export function fetchHedgeCalibration(
  portfolioId: string,
  points = 18,
  step = 21,
): Promise<HedgeCalibration> {
  return getJSON<HedgeCalibration>(
    `/portfolios/${portfolioId}/hedge-backtest/calibrate?points=${points}&step=${step}`,
  );
}

export function fetchBetaDiagnostics(tickers: string[]): Promise<BetaDiagnostics> {
  return getJSON<BetaDiagnostics>(`/securities/beta-diagnostics`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tickers }),
  });
}

export function ingestFactors(): Promise<FactorIngestResult> {
  return getJSON<FactorIngestResult>(`/factors/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

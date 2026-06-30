import type {
  FactorMonitor,
  Frontier,
  HedgeProposal,
  PositionRow,
  RiskSummary,
  StressReport,
  TradeAction,
  TransactionRow,
} from "../types";

// ── Backend base URL resolution ───────────────────────────────────────────────
// In a plain browser (dev), requests are relative and Vite's proxy forwards them to the
// FastAPI backend on :8000 — so the base is "". Inside the Electron shell there is no proxy
// (and a packaged build loads from a file:// origin), so we resolve an absolute base from the
// preload bridge (window.neptune.getApiBaseUrl(), e.g. http://127.0.0.1:8433). The result is
// cached after the first lookup.
interface NeptuneBridge {
  getApiBaseUrl(): Promise<string>;
  // Electron config (neptune-config.json in userData): the bootstrap that determines which DB the
  // sidecar connects to. Works even when the backend is offline — no chicken-and-egg.
  getConfig(): Promise<Record<string, unknown>>;
  saveConfig(cfg: Record<string, unknown>): Promise<{ ok: boolean }>;
  testDbConnection(db: { host: string; port: number; database: string; user: string; password: string }): Promise<{ ok: boolean; message: string }>;
  // Frameless-window controls (Electron only; absent in a plain browser).
  minimizeWindow?(): Promise<void>;
  toggleMaximizeWindow?(): Promise<boolean>;
  closeWindow?(): Promise<void>;
  // Test Mode: relaunch the backend on throwaway SQLite (+ seeded demo data), or back on Postgres.
  isTestMode?(): Promise<boolean>;
  startTestMode?(): Promise<boolean>;
  stopTestMode?(): Promise<boolean>;
}
declare global {
  interface Window {
    neptune?: NeptuneBridge;
  }
}

let _base: string | null = null;

async function apiBaseUrl(): Promise<string> {
  if (_base !== null) return _base;
  try {
    const bridge = typeof window !== "undefined" ? window.neptune : undefined;
    _base = bridge ? (await bridge.getApiBaseUrl()) || "" : "";
  } catch {
    _base = "";
  }
  return _base;
}

async function getJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const base = await apiBaseUrl();
  const res = await fetch(`${base}${url}`, init);
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export interface HealthResponse {
  status: string;
  beta_tol: number;
}

/** Backend liveness — drives the TitleBar status pill. */
export function fetchHealth(): Promise<HealthResponse> {
  return getJSON<HealthResponse>("/health");
}

export function fetchRisk(portfolioId: string): Promise<RiskSummary> {
  return getJSON<RiskSummary>(`/portfolios/${portfolioId}/risk`);
}

export function fetchPositions(portfolioId: string): Promise<PositionRow[]> {
  return getJSON<PositionRow[]>(`/portfolios/${portfolioId}/positions`);
}

export function fetchTransactions(
  portfolioId: string,
  limit = 200,
): Promise<TransactionRow[]> {
  return getJSON<TransactionRow[]>(
    `/portfolios/${portfolioId}/transactions?limit=${limit}`,
  );
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
  firm_id?: string | null;
  investor_entity_id?: string | null;
  lead_pm_ids?: string[];
}

export function fetchPortfolios(): Promise<PortfolioMeta[]> {
  return getJSON<PortfolioMeta[]>("/portfolios");
}

export interface PortfolioInput {
  name: string;
  mandate: "LONG_SHORT" | "LONG_ONLY";
  firm_id?: string | null;
  investor_entity_id?: string | null;
  lead_pm_ids?: string[];
}

/** Create a book. The id is derived server-side from the name when omitted. */
export function createPortfolio(body: PortfolioInput): Promise<PortfolioMeta> {
  return getJSON<PortfolioMeta>("/portfolios", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Remove a book. The server refuses (409) a book that still holds open positions. */
export function deletePortfolio(id: string): Promise<{ deleted: string }> {
  return getJSON(`/portfolios/${id}`, { method: "DELETE" });
}

export interface FirmRow {
  id: string;
  name: string;
  is_internal: boolean;
}
export interface PersonRow {
  id: string;
  firm_id: string;
  name: string;
  role: string;
  email: string | null;
  is_active: boolean;
}
export interface EntityRow {
  id: string;
  firm_id: string;
  name: string;
  base_currency: string;
}

export function fetchFirms(): Promise<FirmRow[]> {
  return getJSON<FirmRow[]>("/firms");
}
export function fetchPeople(): Promise<PersonRow[]> {
  return getJSON<PersonRow[]>("/people");
}
export function fetchEntities(): Promise<EntityRow[]> {
  return getJSON<EntityRow[]>("/investor-entities");
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

/** Book the explicit delta legs from the delta-aware Trade grid (BUY = cover, SELL = open). */
export function bookHedgeLegs(
  portfolioId: string,
  legs: { ticker: string; action: TradeAction; shares: number; price: number }[],
): Promise<{ booked: number; opened: number; covered: number; realized_pnl: number }> {
  return getJSON(`/portfolios/${portfolioId}/hedge/legs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ legs }),
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
  CredentialRow,
  ApiKeyInput,
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

// --- Settings: data-provider API keys (write-only secrets) ---

export function fetchCredentials(): Promise<CredentialRow[]> {
  return getJSON<CredentialRow[]>(`/settings/credentials`);
}

export function saveCredential(
  provider: string,
  body: ApiKeyInput,
): Promise<CredentialRow> {
  return getJSON<CredentialRow>(`/settings/credentials/${provider}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export interface MacroIngestResult {
  ingested: Record<string, number>;
  total: number;
  series: number;
}

export function ingestMacro(startYear = 2000): Promise<MacroIngestResult> {
  return getJSON<MacroIngestResult>(`/macro/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ start_year: startYear }),
  });
}

export interface MacroCatalogRow {
  series_id: string;
  name: string;
  category: string;
  series_class: string;
  frequency: string;
  units: string;
  source: string;
  source_code: string | null;
  description: string;
  ingestable: boolean;
  points: number;
  last_date: string | null;
}

export function fetchMacroCatalog(): Promise<{ series: MacroCatalogRow[]; total: number }> {
  return getJSON<{ series: MacroCatalogRow[]; total: number }>(`/macro/catalog`);
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

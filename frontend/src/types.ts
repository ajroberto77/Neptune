export type Status = "OK" | "WATCH" | "BREACH";

export interface FactorStatus {
  factor: string;
  exposure: number;
  limit: number;
  status: Status;
}

export interface RiskSummary {
  portfolio_id: string;
  net_beta: number;
  beta_tol: number;
  beta_status: Status;
  beta_neutral: boolean;
  long_aum: number;
  headline: string;
  factors: FactorStatus[];
}

export interface PnL {
  day: number;
  total: number;
  unrealized: number;
  realized: number;
}

export interface PositionRow {
  id: number | null;
  ticker: string;
  side: "LONG" | "SHORT";
  short_type: string;
  book: string;
  notional: number;
  quantity: number;
  price: number;
  beta: number;
  beta_method?: string; // "pipeline" or "forward_override"
  cost_basis_method?: string;
  pnl: PnL;
}

export type TradeAction = "BUY" | "SELL";

export interface TransactionInput {
  ticker: string;
  action: TradeAction; // direction; side & open/close/cover are derived by netting
  quantity: number;
  price: number; // execution (average) price per share
  fee_per_share?: number; // transaction fee per share
  trade_date: string; // YYYY-MM-DD
  sector?: string | null;
}

export interface PortfolioPnL {
  portfolio_id: string;
  total: PnL;
  by_book: Record<string, PnL>;
}

export interface ProposedShort {
  ticker: string;
  notional: number;
  beta: number;
  sector?: string | null;
}

export interface SectorConcentration {
  sector: string;
  notional: number;
  fraction: number;
  limit: number;
  breach: boolean;
}

export interface HedgeProposal {
  portfolio_id: string;
  status: string;
  net_beta_before: number;
  net_beta_after: number;
  long_aum: number;
  proposed_shorts: ProposedShort[];
  sector_limit: number;
  sector_breaches: string[];
  sectors: SectorConcentration[];
}

export interface FrontierPoint {
  n_cap: number;
  n_selected: number;
  net_beta_after: number;
  tracking_error: number;
  beta_within_tol: boolean;
}

export interface Frontier {
  portfolio_id: string;
  net_beta_before: number;
  frontier: FrontierPoint[];
}

export interface ScenarioResult {
  name: string;
  market_shock: number;
  total_pnl: number;
  by_book: Record<string, number>;
}

export interface VaR {
  method: string;
  confidence: number;
  horizon_days: number;
  volatility: number;
  var: number;
  expected_shortfall: number;
  n_observations: number;
}

export interface StressReport {
  portfolio_id: string;
  scenarios: ScenarioResult[];
  var: VaR;
  var_methods: VaR[];
}

// --- Settings: configurable database connections ---
export interface ConnectionRow {
  role: string;
  host?: string;
  port?: number;
  database?: string;
  username?: string;
  sslmode?: string | null;
  driver?: string;
  has_password?: boolean;
  configured: boolean;
  bootstrap?: boolean;
}

export interface ConnectionInput {
  host: string;
  port: number;
  database: string;
  username: string;
  password?: string | null;
  sslmode?: string | null;
  driver?: string | null;
}

export interface SyncResult {
  synced: number;
  source: string;
}

export interface IngestRow {
  ticker: string | null;
  prices: number;
  dividends: number;
  corporate_actions: number;
}

export interface IngestResult {
  start: string;
  end: string;
  ingested: IngestRow[];
  errors: { ticker: string; error: string }[];
}

export interface FactorIngestResult {
  start: string;
  end: string;
  counts: Record<string, number>;
}

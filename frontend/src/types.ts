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
  price: number | null; // null when the name isn't priced yet
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
  shares?: number | null;
  price?: number | null;
  notional: number;
  beta: number;
  sector?: string | null;
}

/** An approved-but-not-yet-booked hedge basket, handed to the Trade tab for review + booking. */
export interface PendingHedge {
  portfolioId: string;
  shorts: {
    ticker: string;
    shares: number;
    price: number;
    sector: string | null;
    beta: number;
    notional: number;
  }[];
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

/** Masked status of an external data-provider API key — never the key itself. */
export interface CredentialRow {
  provider: string;
  has_key: boolean;
  source: "stored" | "env" | "none";
}

export interface ApiKeyInput {
  api_key: string;
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

export interface BetaDiagRow {
  ticker: string;
  status: "ok" | "insufficient_data" | "no_prices";
  bars?: number;
  first_bar?: string | null;
  last_bar?: string | null;
  obs_used?: number;
  gap_days?: number;
  starts_after_benchmark?: boolean;
  beta?: number;
  beta_raw?: number;
  var_ols?: number;
  vasicek_weight?: number;
  note?: string;
}

export interface BetaStats {
  last: number;
  mean: number;
  std: number;
  min: number;
  max: number;
  range: number;
  points: number;
}

export interface BetaHistoryPosition {
  ticker: string;
  side: string;
  short_type: string;
  notional: number;
  stats: BetaStats | null;
  series: { date: string; beta: number; beta_raw: number; n_obs: number }[];
}

export interface BetaHistory {
  portfolio_id: string;
  lookback: number;
  points: number;
  step: number;
  net: { date: string; net_beta: number }[];
  net_stats: BetaStats | null;
  positions: BetaHistoryPosition[];
}

export interface HedgeBacktestPoint {
  date: string;
  target_net_beta: number;
  realized_net_beta: number;
  n_names: number;
  turnover: number;
}

export interface HedgeBacktest {
  portfolio_id: string;
  tol: number;
  rebalance_step: number;
  rmse: number;
  coverage: number;
  mean_abs_realized: number;
  mean_turnover: number;
  n_rebalances: number;
  points: HedgeBacktestPoint[];
}

export interface CalibrationRow {
  beta_add_budget: number;
  rmse: number;
  coverage: number;
  mean_abs_realized: number;
  mean_turnover: number;
  n_rebalances: number;
}

export interface HedgeCalibration {
  portfolio_id: string;
  tol: number;
  current_budget: number;
  grid: CalibrationRow[];
}

export interface FactorMonitor {
  portfolio_id: string;
  available: boolean;
  long_aum: number;
  // Report-only net exposures (the optimizer does NOT neutralize these).
  factors: Record<string, number>;   // IVOL / BAB / AMIHUD -> net loading
  sectors: Record<string, number>;   // sector -> net signed-notional weight
}

export interface BetaDiagnostics {
  benchmark: { ticker: string; bars: number; first_bar: string; last_bar: string; obs_used: number };
  min_obs: number;
  names: BetaDiagRow[];
}

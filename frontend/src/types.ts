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
  unrealised: number;
  realised: number;
}

export interface PositionRow {
  ticker: string;
  side: "LONG" | "SHORT";
  short_type: string;
  book: string;
  notional: number;
  beta: number;
  beta_method?: string; // "pipeline" or "forward_override"
  cost_basis_method?: string;
  pnl: PnL;
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

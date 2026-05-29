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

export interface PositionRow {
  ticker: string;
  side: "LONG" | "SHORT";
  notional: number;
  short_type: string;
  beta: number;
}

export interface ProposedShort {
  ticker: string;
  notional: number;
  beta: number;
}

export interface HedgeProposal {
  portfolio_id: string;
  status: string;
  net_beta_before: number;
  net_beta_after: number;
  long_aum: number;
  proposed_shorts: ProposedShort[];
}

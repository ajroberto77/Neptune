import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Hedge } from "./Hedge";
import type { Frontier, HedgeProposal } from "../types";

const proposal: HedgeProposal = {
  portfolio_id: "IRIDIUM-CORE",
  status: "PENDING_APPROVAL",
  net_beta_before: 0.94,
  net_beta_after: 0.0123,
  long_aum: 2_500_000,
  proposed_shorts: [
    { ticker: "HDG1", shares: 1200, price: 250, notional: 300000, beta: 1.1, sector: "Technology" },
  ],
  sector_limit: 0.3,
  sector_breaches: ["Technology"],
  sectors: [
    { sector: "Technology", notional: 300000, fraction: 0.6, limit: 0.3, breach: true },
    { sector: "Energy", notional: 200000, fraction: 0.4, limit: 0.3, breach: true },
  ],
};

const frontier: Frontier = {
  portfolio_id: "IRIDIUM-CORE",
  net_beta_before: 0.94,
  frontier: [
    { n_cap: 10, n_selected: 10, net_beta_after: 0.0, tracking_error: 0.0005, beta_within_tol: true },
    { n_cap: 20, n_selected: 18, net_beta_after: 0.0, tracking_error: 0.0002, beta_within_tol: true },
  ],
};

const base = {
  portfolios: [{ id: "P-A", name: "Book A" }],
  hedgePortfolioId: "P-A",
  onHedgePortfolio: () => {},
  onPropose: () => {},
  proposing: false,
  onApprove: () => {},
  onReject: () => {},
  approving: false,
  frontier: null as Frontier | null,
  onFrontier: () => {},
  frontierLoading: false,
};

describe("Hedge", () => {
  it("invokes onPropose when the button is clicked", () => {
    const onPropose = vi.fn();
    render(<Hedge {...base} proposal={null} onPropose={onPropose} />);
    fireEvent.click(screen.getByText("Propose hedge"));
    expect(onPropose).toHaveBeenCalledOnce();
  });

  it("renders the basket with shares + beta-adj notional and a single approve/reject", () => {
    const onApprove = vi.fn();
    render(<Hedge {...base} proposal={proposal} onApprove={onApprove} />);
    expect(screen.getByText("HDG1")).toBeInTheDocument();
    expect(screen.getByText("1,200")).toBeInTheDocument(); // shares to short
    // ONE basket-level approve (not per name); clicking it fires onApprove.
    fireEvent.click(screen.getByText("Approve basket"));
    expect(onApprove).toHaveBeenCalledOnce();
    // Weighted-average beta total (1.1 with a single name) renders.
    expect(screen.getAllByText("1.10").length).toBeGreaterThanOrEqual(1);
  });

  it("renders the complexity-quality frontier rows when present", () => {
    render(<Hedge {...base} proposal={null} frontier={frontier} />);
    expect(screen.getByText("Complexity-Quality Frontier")).toBeInTheDocument();
    expect(screen.getByText(/≤ 10/)).toBeInTheDocument();
    expect(screen.getByText(/≤ 20/)).toBeInTheDocument();
  });

  it("shows the sector breakdown and feeds the header limit into Propose", () => {
    const onPropose = vi.fn();
    render(<Hedge {...base} proposal={proposal} onPropose={onPropose} />);
    expect(screen.getByText("Sector Concentration")).toBeInTheDocument(); // breakdown below
    expect(screen.getAllByText("BREACH").length).toBeGreaterThanOrEqual(2);
    // The sector limit lives in the proposal header now; Propose carries it.
    fireEvent.change(screen.getByLabelText("sector-limit-input"), { target: { value: "20" } });
    fireEvent.click(screen.getByText("Propose hedge"));
    expect(onPropose).toHaveBeenCalledWith(0.2, undefined);
  });
});

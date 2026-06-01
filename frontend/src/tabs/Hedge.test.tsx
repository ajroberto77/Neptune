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
  proposed_shorts: [{ ticker: "HDG1", notional: 300000, beta: 1.1, sector: "Technology" }],
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
  onPropose: () => {},
  proposing: false,
  onApplySectorLimit: () => {},
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

  it("renders the proposed short basket and the pending status", () => {
    render(<Hedge {...base} proposal={proposal} />);
    expect(screen.getByText("HDG1")).toBeInTheDocument();
    expect(screen.getByText("PENDING_APPROVAL")).toBeInTheDocument();
    // Approve/Reject controls are present (advisory — Neptune never executes).
    expect(screen.getByText("Approve")).toBeInTheDocument();
    expect(screen.getByText("Reject")).toBeInTheDocument();
  });

  it("renders the complexity-quality frontier rows when present", () => {
    render(<Hedge {...base} proposal={null} frontier={frontier} />);
    expect(screen.getByText("Complexity-Quality Frontier")).toBeInTheDocument();
    expect(screen.getByText(/≤ 10/)).toBeInTheDocument();
    expect(screen.getByText(/≤ 20/)).toBeInTheDocument();
  });

  it("shows the sector concentration panel and applies a new limit", () => {
    const onApplySectorLimit = vi.fn();
    render(<Hedge {...base} proposal={proposal} onApplySectorLimit={onApplySectorLimit} />);
    expect(screen.getByText("Sector Concentration")).toBeInTheDocument();
    expect(screen.getAllByText("BREACH").length).toBeGreaterThanOrEqual(2);
    fireEvent.change(screen.getByLabelText("sector-limit-input"), { target: { value: "20" } });
    fireEvent.click(screen.getByText("Apply"));
    expect(onApplySectorLimit).toHaveBeenCalledWith(0.2);
  });
});

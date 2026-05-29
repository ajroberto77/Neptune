import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { RiskDashboard } from "./RiskDashboard";
import type { HedgeProposal, RiskSummary } from "../types";

const summary: RiskSummary = {
  portfolio_id: "IRIDIUM-CORE",
  net_beta: 0.94,
  beta_tol: 0.05,
  beta_status: "BREACH",
  beta_neutral: false,
  long_aum: 2_500_000,
  headline: "Net beta +0.9400 (OUTSIDE tolerance; limit +/-0.05). 0 factor breach(es).",
  factors: [
    { factor: "MKT", exposure: 0.0, limit: 0.2, status: "OK" },
    { factor: "SMB", exposure: 0.0, limit: 0.2, status: "OK" },
  ],
};

const proposal: HedgeProposal = {
  portfolio_id: "IRIDIUM-CORE",
  status: "PENDING_APPROVAL",
  net_beta_before: 0.94,
  net_beta_after: 0.0123,
  long_aum: 2_500_000,
  proposed_shorts: [{ ticker: "HDG1", notional: 300000, beta: 1.1 }],
};

describe("RiskDashboard", () => {
  it("shows the net beta and a BREACH badge when outside tolerance", () => {
    render(
      <RiskDashboard summary={summary} proposal={null} onPropose={() => {}} proposing={false} />,
    );
    expect(screen.getByLabelText("net-beta-value")).toHaveTextContent("+0.9400");
    expect(screen.getByText("BREACH")).toBeInTheDocument();
  });

  it("invokes onPropose when the button is clicked", () => {
    const onPropose = vi.fn();
    render(
      <RiskDashboard summary={summary} proposal={null} onPropose={onPropose} proposing={false} />,
    );
    fireEvent.click(screen.getByText("Propose hedge"));
    expect(onPropose).toHaveBeenCalledOnce();
  });

  it("renders the proposed short basket and the pending status", () => {
    render(
      <RiskDashboard
        summary={summary}
        proposal={proposal}
        onPropose={() => {}}
        proposing={false}
      />,
    );
    expect(screen.getByText("HDG1")).toBeInTheDocument();
    expect(screen.getByText("PENDING_APPROVAL")).toBeInTheDocument();
  });
});

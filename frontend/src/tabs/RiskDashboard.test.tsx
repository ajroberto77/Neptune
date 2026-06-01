import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { RiskDashboard } from "./RiskDashboard";
import type { RiskSummary } from "../types";

const summary: RiskSummary = {
  portfolio_id: "IRIDIUM-CORE",
  net_beta: 0.94,
  beta_tol: 0.05,
  beta_status: "BREACH",
  beta_neutral: false,
  long_aum: 2_500_000,
  headline: "Net beta +0.9400 (OUTSIDE tolerance; limit +/-0.05). 0 factor breach(es).",
  factors: [
    { factor: "SMB", exposure: 0.04, limit: 0.2, status: "OK" },
    { factor: "HML", exposure: 0.0, limit: 0.2, status: "OK" },
  ],
};

describe("RiskDashboard", () => {
  it("shows the net beta and a BREACH badge when outside tolerance", () => {
    render(<RiskDashboard summary={summary} />);
    expect(screen.getByLabelText("net-beta-value")).toHaveTextContent("+0.9400");
    expect(screen.getByText("BREACH")).toBeInTheDocument();
  });
});

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Stress } from "./Stress";
import type { StressReport } from "../types";

const report: StressReport = {
  portfolio_id: "IRIDIUM-CORE",
  scenarios: [
    {
      name: "Market -10%",
      market_shock: -0.1,
      total_pnl: -235000,
      by_book: { LONG: -285000, SYSTEMATIC_SHORT: 0, DISCRETIONARY_SHORT: 50000 },
    },
  ],
  var: {
    method: "parametric",
    confidence: 0.95,
    horizon_days: 1,
    volatility: 23898,
    var: 39308,
    expected_shortfall: 49294,
    n_observations: 0,
  },
  var_methods: [
    {
      method: "parametric",
      confidence: 0.95,
      horizon_days: 1,
      volatility: 23898,
      var: 39308,
      expected_shortfall: 49294,
      n_observations: 0,
    },
    {
      method: "historical",
      confidence: 0.95,
      horizon_days: 1,
      volatility: 23100,
      var: 37365,
      expected_shortfall: 47478,
      n_observations: 300,
    },
    {
      method: "monte_carlo",
      confidence: 0.95,
      horizon_days: 1,
      volatility: 23850,
      var: 38997,
      expected_shortfall: 48928,
      n_observations: 50000,
    },
  ],
};

describe("Stress", () => {
  it("runs stress when the button is clicked", () => {
    const onRun = vi.fn();
    render(<Stress report={null} onRun={onRun} loading={false} />);
    fireEvent.click(screen.getByText("Run stress"));
    expect(onRun).toHaveBeenCalledOnce();
  });

  it("renders all three VaR methods and the scenario rows", () => {
    render(<Stress report={report} onRun={() => {}} loading={false} />);
    expect(screen.getByText(/Value at Risk/)).toBeInTheDocument();
    // The three VaR methods appear side by side.
    expect(screen.getByText("Parametric")).toBeInTheDocument();
    expect(screen.getByText("Historical simulation")).toBeInTheDocument();
    expect(screen.getByText("Monte Carlo")).toBeInTheDocument();
    expect(screen.getByText("$39,308")).toBeInTheDocument(); // parametric VaR
    expect(screen.getByText("$37,365")).toBeInTheDocument(); // historical VaR
    // Scenario row + signed total P&L.
    expect(screen.getByText("Market -10%")).toBeInTheDocument();
    expect(screen.getByText("-$235,000")).toBeInTheDocument();
  });
});

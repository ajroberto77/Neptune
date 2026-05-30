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
    confidence: 0.95,
    horizon_days: 1,
    volatility: 23898,
    var: 39308,
    expected_shortfall: 49294,
  },
};

describe("Stress", () => {
  it("runs stress when the button is clicked", () => {
    const onRun = vi.fn();
    render(<Stress report={null} onRun={onRun} loading={false} />);
    fireEvent.click(screen.getByText("Run stress"));
    expect(onRun).toHaveBeenCalledOnce();
  });

  it("renders VaR and the scenario rows when a report is present", () => {
    render(<Stress report={report} onRun={() => {}} loading={false} />);
    expect(screen.getByText("Value at Risk")).toBeInTheDocument();
    expect(screen.getByText("Market -10%")).toBeInTheDocument();
    // Total P&L of the scenario is shown, signed.
    expect(screen.getByText("-$235,000")).toBeInTheDocument();
    // VaR figure.
    expect(screen.getByText("$39,308")).toBeInTheDocument();
  });
});

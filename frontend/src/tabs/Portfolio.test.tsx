import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { Portfolio } from "./Portfolio";
import type { PositionRow } from "../types";

function pos(over: Partial<PositionRow>): PositionRow {
  return {
    id: 1,
    ticker: "AAA",
    side: "LONG",
    short_type: "NA",
    book: "LONG",
    notional: 1_000_000,
    quantity: 1000,
    price: 204.13,
    beta: 1.2,
    beta_method: "forward_override",
    cost_basis_method: "FIFO",
    pnl: { day: 0, total: 0, unrealized: 0, realized: 0 },
    ...over,
  };
}

describe("Portfolio", () => {
  it("groups shorts into Systematic and Discretionary subgroups with subtotals (I-03)", () => {
    const positions = [
      pos({ ticker: "AAA", side: "LONG" }),
      pos({ ticker: "SYS1", side: "SHORT", short_type: "SYSTEMATIC" }),
      pos({ ticker: "DSC1", side: "SHORT", short_type: "DISCRETIONARY" }),
    ];
    render(<Portfolio positions={positions} />);
    expect(screen.getByText("Longs")).toBeInTheDocument();
    expect(screen.getByText("Shorts")).toBeInTheDocument();
    expect(screen.getByText("SYS1")).toBeInTheDocument();
    expect(screen.getByText("DSC1")).toBeInTheDocument();
    // The two books are split into labelled subgroups (never conflated) — no per-row tags.
    expect(screen.getByText(/Systematic Shorts/)).toBeInTheDocument();
    expect(screen.getByText(/Discretionary Shorts/)).toBeInTheDocument();
    // With both books present, each gets a subtotal alongside the grand Total Short.
    expect(screen.getByText("Total Systematic")).toBeInTheDocument();
    expect(screen.getByText("Total Discretionary")).toBeInTheDocument();
    expect(screen.getByText("Total Short")).toBeInTheDocument();
    expect(screen.queryByText("systematic")).not.toBeInTheDocument(); // old tag gone
  });

  it("renders signed P&L for a position", () => {
    render(
      <Portfolio
        positions={[pos({ pnl: { day: -100, total: 5000, unrealized: 5000, realized: 0 } })]}
      />,
    );
    // Day P&L shows on the row, the Total-Long footer, and the Net Position section.
    expect(screen.getAllByText("-$100").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("+$5,000").length).toBeGreaterThanOrEqual(2);
    // Per-share price renders with cents.
    expect(screen.getByText("$204.13")).toBeInTheDocument();
  });

  it("shows a Net Position on top, totals, and weighted-avg beta", () => {
    render(
      <Portfolio
        positions={[
          pos({ ticker: "AAA", side: "LONG", notional: 1_000_000, beta: 1.0 }),
          pos({ ticker: "HDG", side: "SHORT", short_type: "SYSTEMATIC", notional: 1_000_000, beta: 1.0 }),
        ]}
      />,
    );
    // Long beta-adj = +1,000,000; short beta-adj = -1,000,000; net beta-adj ~ $0.
    expect(screen.getByText("Net Position")).toBeInTheDocument();
    expect(screen.getByText("Net")).toBeInTheDocument(); // the net row label
    expect(screen.getAllByText("+$0").length).toBeGreaterThanOrEqual(1); // net beta-adj nets out
    expect(screen.getByText("Total Long")).toBeInTheDocument();
    expect(screen.getByText("Total Short")).toBeInTheDocument();
    // Weighted-average beta in the totals (1.00 for a single beta-1 name).
    expect(screen.getAllByText("1.00").length).toBeGreaterThanOrEqual(1);
  });

  it("shows an empty message for a section with no positions", () => {
    render(<Portfolio positions={[pos({ side: "LONG" })]} />);
    // Only the Shorts section is empty.
    expect(screen.getAllByText("No positions.").length).toBe(1);
  });

  it("triggers a manual price refresh from the live-pricing control", async () => {
    const { fireEvent } = await import("@testing-library/react");
    const onRefreshNow = vi.fn();
    render(
      <Portfolio
        positions={[pos({})]}
        refreshMins={10}
        onChangeMins={() => {}}
        onRefreshNow={onRefreshNow}
        lastPriced={null}
        pricing={false}
      />,
    );
    fireEvent.click(screen.getByText("Refresh now"));
    expect(onRefreshNow).toHaveBeenCalledOnce();
  });
});

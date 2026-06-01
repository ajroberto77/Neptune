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
    pnl: { day: 0, total: 0, unrealised: 0, realised: 0 },
    ...over,
  };
}

describe("Portfolio", () => {
  it("separates the three books (invariant I-03)", () => {
    const positions = [
      pos({ ticker: "AAA", book: "LONG" }),
      pos({ ticker: "SYS1", book: "SYSTEMATIC_SHORT", side: "SHORT", short_type: "SYSTEMATIC" }),
      pos({ ticker: "DSC1", book: "DISCRETIONARY_SHORT", side: "SHORT", short_type: "DISCRETIONARY" }),
    ];
    render(<Portfolio positions={positions} />);
    expect(screen.getByText("Long Book")).toBeInTheDocument();
    expect(screen.getByText("Systematic Short")).toBeInTheDocument();
    expect(screen.getByText("Discretionary Short")).toBeInTheDocument();
    expect(screen.getByText("SYS1")).toBeInTheDocument();
    expect(screen.getByText("DSC1")).toBeInTheDocument();
  });

  it("renders signed P&L for a position", () => {
    render(
      <Portfolio
        positions={[pos({ pnl: { day: -100, total: 5000, unrealised: 5000, realised: 0 } })]}
      />,
    );
    expect(screen.getByText("-$100")).toBeInTheDocument();
    // Unrealised and Total both show +$5,000.
    expect(screen.getAllByText("+$5,000").length).toBe(2);
    // Per-share price renders with cents.
    expect(screen.getByText("$204.13")).toBeInTheDocument();
  });

  it("shows an empty message for a book with no positions", () => {
    render(<Portfolio positions={[pos({ book: "LONG" })]} />);
    // Systematic and discretionary books are empty.
    expect(screen.getAllByText("No positions.").length).toBe(2);
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

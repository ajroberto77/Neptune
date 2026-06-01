import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Blotter } from "./Blotter";
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
    beta: 1.2,
    beta_method: "forward_override",
    cost_basis_method: "FIFO",
    pnl: { day: 0, total: 0, unrealised: 0, realised: 0 },
    ...over,
  };
}

describe("Blotter", () => {
  it("separates the three books (invariant I-03)", () => {
    const positions = [
      pos({ ticker: "AAA", book: "LONG" }),
      pos({ ticker: "SYS1", book: "SYSTEMATIC_SHORT", side: "SHORT", short_type: "SYSTEMATIC" }),
      pos({ ticker: "DSC1", book: "DISCRETIONARY_SHORT", side: "SHORT", short_type: "DISCRETIONARY" }),
    ];
    render(<Blotter positions={positions} />);
    expect(screen.getByText("Long Book")).toBeInTheDocument();
    expect(screen.getByText("Systematic Short")).toBeInTheDocument();
    expect(screen.getByText("Discretionary Short")).toBeInTheDocument();
    expect(screen.getByText("SYS1")).toBeInTheDocument();
    expect(screen.getByText("DSC1")).toBeInTheDocument();
  });

  it("renders signed P&L for a position", () => {
    render(
      <Blotter
        positions={[pos({ pnl: { day: -100, total: 5000, unrealised: 5000, realised: 0 } })]}
      />,
    );
    expect(screen.getByText("-$100")).toBeInTheDocument();
    // Unrealised and Total both show +$5,000.
    expect(screen.getAllByText("+$5,000").length).toBe(2);
  });

  it("shows an empty message for a book with no positions", () => {
    render(<Blotter positions={[pos({ book: "LONG" })]} />);
    // Systematic and discretionary books are empty.
    expect(screen.getAllByText("No positions.").length).toBe(2);
  });
});

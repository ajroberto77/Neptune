import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Trade } from "./Trade";

const PORTFOLIOS = [
  { id: "P-A", name: "Portfolio A" },
  { id: "P-B", name: "Portfolio B" },
];

function fillRow(row: HTMLElement, fields: { ticker: string; qty: string; price: string }) {
  fireEvent.change(within(row).getByLabelText("Ticker"), { target: { value: fields.ticker } });
  fireEvent.change(within(row).getByLabelText("Quantity"), { target: { value: fields.qty } });
  fireEvent.change(within(row).getByLabelText("Average Price"), { target: { value: fields.price } });
}

describe("Trade grid", () => {
  it("submits each row to its own chosen portfolio (per-row mode)", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const onAfterBatch = vi.fn().mockResolvedValue(undefined);
    render(
      <Trade
        portfolios={PORTFOLIOS}
        defaultPortfolioId="__consolidated__"  // not a real portfolio -> per-row by default
        onSubmit={onSubmit}
        onAfterBatch={onAfterBatch}
        busy={false}
      />,
    );

    // One row to start; add a second.
    fireEvent.click(screen.getByText("+ Add row"));
    const rows = screen.getAllByLabelText("Ticker").map((i) => i.closest("tr")!);
    fillRow(rows[0], { ticker: "msft", qty: "10", price: "100" });
    fillRow(rows[1], { ticker: "aapl", qty: "5", price: "200" });
    // Point the second row at Portfolio B.
    fireEvent.change(within(rows[1]).getByLabelText("Portfolio"), { target: { value: "P-B" } });

    fireEvent.click(screen.getByText("Submit all"));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
    const calls = Object.fromEntries(onSubmit.mock.calls.map(([pid, t]) => [t.ticker, pid]));
    expect(calls).toEqual({ MSFT: "P-A", AAPL: "P-B" });
    await waitFor(() => expect(onAfterBatch).toHaveBeenCalled());
  });

  it("allocate-all sends every row to one portfolio", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <Trade
        portfolios={PORTFOLIOS}
        defaultPortfolioId=""
        onSubmit={onSubmit}
        onAfterBatch={vi.fn().mockResolvedValue(undefined)}
        busy={false}
      />,
    );
    // Choose "Allocate all to" Portfolio B.
    fireEvent.change(screen.getByLabelText("Allocate all to"), { target: { value: "P-B" } });
    const row = screen.getByLabelText("Ticker").closest("tr")!;
    fillRow(row, { ticker: "nvda", qty: "1", price: "900" });
    fireEvent.click(screen.getByText("Submit all"));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0][0]).toBe("P-B");
  });

  it("flags a failed row and keeps it; clears the successful one", async () => {
    const onSubmit = vi
      .fn()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("boom"));
    render(
      <Trade
        portfolios={PORTFOLIOS}
        defaultPortfolioId="P-A"
        onSubmit={onSubmit}
        onAfterBatch={vi.fn().mockResolvedValue(undefined)}
        busy={false}
      />,
    );
    fireEvent.click(screen.getByText("+ Add row"));
    const rows = screen.getAllByLabelText("Ticker").map((i) => i.closest("tr")!);
    fillRow(rows[0], { ticker: "ok", qty: "1", price: "10" });
    fillRow(rows[1], { ticker: "bad", qty: "1", price: "10" });

    fireEvent.click(screen.getByText("Submit all"));
    // Only the failed row remains, flagged.
    await waitFor(() => expect(screen.getAllByLabelText("Ticker")).toHaveLength(1));
    expect((screen.getByLabelText("Ticker") as HTMLInputElement).value).toBe("bad");
  });
});

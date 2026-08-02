import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Blotter } from "./Trade";
import type { TransactionRow } from "../types";

const fetchTransactions = vi.fn<() => Promise<TransactionRow[]>>();

vi.mock("../api/client", () => ({
  fetchTransactions: () => fetchTransactions(),
}));

const ROW: TransactionRow = {
  id: 1,
  portfolio_id: "P-A",
  ticker: "AAPL",
  action: "BUY",
  quantity: 10,
  price: 200,
  trade_date: "2026-01-02",
  short_type: "NA",
  origin: "MANUAL",
  realized_pnl: 0,
  effect: "Open/add long",
};

describe("Blotter", () => {
  beforeEach(() => {
    window.localStorage.clear();
    fetchTransactions.mockResolvedValue([ROW]);
  });

  it("shows all 8 original columns in the original order by default (regression guard)", async () => {
    render(<Blotter portfolioId="P-A" reloadKey={0} />);
    await waitFor(() => expect(screen.getByText("AAPL")).toBeInTheDocument());
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual([
      "Date", "Ticker", "Action", "Shares", "Price", "Effect", "Realized P&L", "Origin",
    ]);
  });

  it("hides a column when toggled off via the picker", async () => {
    render(<Blotter portfolioId="P-A" reloadKey={0} />);
    await waitFor(() => expect(screen.getByText("AAPL")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Customize columns"));
    fireEvent.click(screen.getByLabelText("Show Origin"));

    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).not.toContain("Origin");
    expect(headers).toHaveLength(7);
  });

  it("reorders columns via the up/down controls", async () => {
    render(<Blotter portfolioId="P-A" reloadKey={0} />);
    await waitFor(() => expect(screen.getByText("AAPL")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Customize columns"));
    fireEvent.click(screen.getByLabelText("Move Ticker up"));

    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers.slice(0, 2)).toEqual(["Ticker", "Date"]);
  });

  it("persists the column choice across a remount", async () => {
    const { unmount } = render(<Blotter portfolioId="P-A" reloadKey={0} />);
    await waitFor(() => expect(screen.getByText("AAPL")).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText("Customize columns"));
    fireEvent.click(screen.getByLabelText("Show Origin"));
    unmount();

    render(<Blotter portfolioId="P-A" reloadKey={0} />);
    await waitFor(() => expect(screen.getByText("AAPL")).toBeInTheDocument());
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).not.toContain("Origin");
  });
});

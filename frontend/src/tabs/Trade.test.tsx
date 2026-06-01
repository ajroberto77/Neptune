import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Trade } from "./Trade";
import type { PositionRow } from "../types";

function pos(over: Partial<PositionRow>): PositionRow {
  return {
    id: 1,
    ticker: "AAA",
    side: "LONG",
    short_type: "NA",
    book: "LONG",
    notional: 50_000,
    quantity: 100,
    beta: 1.2,
    pnl: { day: 0, total: 0, unrealised: 0, realised: 0 },
    ...over,
  };
}

describe("Trade", () => {
  it("records a transaction with the entered fields", async () => {
    const onRecord = vi.fn().mockResolvedValue(undefined);
    render(<Trade positions={[]} onRecord={onRecord} onClose={vi.fn()} busy={false} />);

    fireEvent.change(screen.getByText("Ticker").parentElement!.querySelector("input")!, {
      target: { value: "msft" },
    });
    fireEvent.change(screen.getByText("Quantity").parentElement!.querySelector("input")!, {
      target: { value: "10" },
    });
    fireEvent.change(screen.getByText("Price").parentElement!.querySelector("input")!, {
      target: { value: "100" },
    });
    fireEvent.click(screen.getByText("Record transaction"));

    await waitFor(() => expect(onRecord).toHaveBeenCalledOnce());
    const arg = onRecord.mock.calls[0][0];
    expect(arg.ticker).toBe("MSFT"); // uppercased
    expect(arg.quantity).toBe(10);
    expect(arg.price).toBe(100);
    expect(arg.book).toBe("LONG");
  });

  it("closes a position at the entered exit price", async () => {
    const onClose = vi.fn().mockResolvedValue(undefined);
    render(
      <Trade positions={[pos({ id: 7, quantity: 100 })]} onRecord={vi.fn()} onClose={onClose} busy={false} />,
    );
    // Enter an exit price, then Close all.
    fireEvent.change(screen.getByPlaceholderText("exit price"), { target: { value: "60" } });
    fireEvent.click(screen.getByText("Close all"));
    await waitFor(() => expect(onClose).toHaveBeenCalledWith(7, 100, 60));
  });

  it("only lists positions with recorded lots as closeable", () => {
    render(
      <Trade
        positions={[pos({ ticker: "AAA", quantity: 100 }), pos({ ticker: "NOLOTS", quantity: 0 })]}
        onRecord={vi.fn()}
        onClose={vi.fn()}
        busy={false}
      />,
    );
    expect(screen.getByText("AAA")).toBeInTheDocument();
    expect(screen.queryByText("NOLOTS")).not.toBeInTheDocument();
  });
});

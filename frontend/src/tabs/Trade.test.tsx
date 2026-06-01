import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Trade } from "./Trade";

const BOOKS = [
  { id: "BOOK-A", name: "Book A" },
  { id: "BOOK-B", name: "Book B" },
];

function setField(label: string, value: string) {
  // The batch table reuses some header text (e.g. "Ticker"); pick the form label whose
  // parent <label> actually wraps an input.
  const el = screen
    .getAllByText(label)
    .find((n) => n.parentElement?.querySelector("input"))!;
  fireEvent.change(el.parentElement!.querySelector("input")!, { target: { value } });
}

describe("Trade", () => {
  it("stages a ticket into the batch, then submits it to the chosen book", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const onAfterBatch = vi.fn().mockResolvedValue(undefined);
    render(
      <Trade
        portfolios={BOOKS}
        defaultPortfolioId="BOOK-B"
        onSubmit={onSubmit}
        onAfterBatch={onAfterBatch}
        busy={false}
      />,
    );

    setField("Ticker", "msft");
    setField("Quantity", "10");
    setField("Price", "100");
    fireEvent.click(screen.getByText("Add to batch"));

    // Staged, not yet submitted.
    expect(screen.getByText("MSFT")).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("Submit all"));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    const [targetId, t] = onSubmit.mock.calls[0];
    expect(targetId).toBe("BOOK-B"); // honors the global selection when it's a real book
    expect(t.ticker).toBe("MSFT"); // uppercased
    expect(t.quantity).toBe(10);
    expect(t.action).toBe("BUY");
    await waitFor(() => expect(onAfterBatch).toHaveBeenCalled());
  });

  it("flags a failed ticket and keeps it in the batch", async () => {
    const onSubmit = vi
      .fn()
      .mockResolvedValueOnce(undefined) // first ok
      .mockRejectedValueOnce(new Error("boom")); // second fails
    render(
      <Trade
        portfolios={BOOKS}
        defaultPortfolioId="BOOK-A"
        onSubmit={onSubmit}
        onAfterBatch={vi.fn().mockResolvedValue(undefined)}
        busy={false}
      />,
    );

    setField("Ticker", "aaa");
    setField("Quantity", "1");
    setField("Price", "10");
    fireEvent.click(screen.getByText("Add to batch"));
    setField("Ticker", "bbb");
    setField("Quantity", "1");
    setField("Price", "10");
    fireEvent.click(screen.getByText("Add to batch"));

    fireEvent.click(screen.getByText("Submit all"));
    // The succeeded one drops off; the failed one stays, flagged.
    await waitFor(() => expect(screen.getByText("failed")).toBeInTheDocument());
    expect(screen.queryByText("AAA")).not.toBeInTheDocument();
    expect(screen.getByText("BBB")).toBeInTheDocument();
  });

  it("falls back to a real book when the app is on Consolidated", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <Trade
        portfolios={BOOKS}
        defaultPortfolioId="__consolidated__"
        onSubmit={onSubmit}
        onAfterBatch={vi.fn().mockResolvedValue(undefined)}
        busy={false}
      />,
    );
    setField("Ticker", "xyz");
    setField("Quantity", "1");
    setField("Price", "10");
    fireEvent.click(screen.getByText("Add to batch"));
    fireEvent.click(screen.getByText("Submit all"));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0][0]).toBe("BOOK-A"); // first real book, not consolidated
  });
});

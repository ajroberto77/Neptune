import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Settings } from "./Settings";
import type { ConnectionRow } from "../types";

const rows: ConnectionRow[] = [
  { role: "PORTFOLIO", host: "localhost", port: 5432, database: "neptune_portfolios",
    username: "neptune", has_password: true, configured: true, bootstrap: true },
  { role: "SECURITIES", host: "localhost", port: 5432, database: "neptune_securities",
    username: "neptune", has_password: true, configured: true, bootstrap: false },
  { role: "UNIVERSE", host: "localhost", port: 5434, database: "cato_securities",
    username: "readonly", has_password: true, configured: true, bootstrap: false },
];

const saveConnection = vi.fn(async (_role: string, _body: unknown) => rows[2]);
const testConnection = vi.fn(async (role: string) => ({ role, ok: true }));
const syncUniverse = vi.fn(async () => ({ synced: 42, source: "cato_securities" }));
const ingestPrices = vi.fn(async () => ({
  start: "2025-04-26",
  end: "2026-05-31",
  ingested: [
    { ticker: "AAPL", prices: 252, dividends: 4, corporate_actions: 0 },
    { ticker: "MSFT", prices: 252, dividends: 4, corporate_actions: 1 },
  ],
  errors: [],
}));

vi.mock("../api/client", () => ({
  fetchConnections: () => Promise.resolve(rows),
  saveConnection: (role: string, body: unknown) => saveConnection(role, body),
  testConnection: (role: string) => testConnection(role),
  syncUniverse: () => syncUniverse(),
  ingestPrices: () => ingestPrices(),
}));

describe("Settings", () => {
  beforeEach(() => {
    saveConnection.mockClear();
    testConnection.mockClear();
    syncUniverse.mockClear();
    ingestPrices.mockClear();
  });

  it("renders all three connection roles and flags the bootstrap DB", async () => {
    render(<Settings />);
    expect(await screen.findByText(/Portfolio DB/)).toBeInTheDocument();
    expect(screen.getByText(/Securities DB/)).toBeInTheDocument();
    expect(screen.getByText(/Universe DB/)).toBeInTheDocument();
    // The portfolio DB is marked restart-only.
    expect(screen.getByText(/applies on restart/)).toBeInTheDocument();
  });

  it("saves a connection with a blank password (preserves stored secret)", async () => {
    render(<Settings />);
    await screen.findByText(/Universe DB/);
    // Save the universe row without typing a password.
    const saveButtons = screen.getAllByText("Save");
    fireEvent.click(saveButtons[2]);
    await waitFor(() => expect(saveConnection).toHaveBeenCalled());
    const [role, body] = saveConnection.mock.calls[0];
    expect(role).toBe("UNIVERSE");
    // Blank password is sent as null so the backend leaves the stored secret unchanged.
    expect((body as { password: string | null }).password).toBeNull();
  });

  it("syncs the universe and reports the count", async () => {
    render(<Settings />);
    await screen.findByText(/Universe DB/);
    fireEvent.click(screen.getByText("Sync universe"));
    await waitFor(() => expect(syncUniverse).toHaveBeenCalledOnce());
    expect(await screen.findByText(/Synced 42 securities/)).toBeInTheDocument();
  });

  it("backfills prices and reports total bars across names", async () => {
    render(<Settings />);
    await screen.findByText(/Securities DB/);
    fireEvent.click(screen.getByText("Backfill prices"));
    await waitFor(() => expect(ingestPrices).toHaveBeenCalledOnce());
    // 252 + 252 bars across 2 names.
    expect(
      await screen.findByText(/Ingested 504 price bars across 2 names/),
    ).toBeInTheDocument();
  });
});

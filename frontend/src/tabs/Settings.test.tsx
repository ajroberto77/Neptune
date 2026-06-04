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
const ingestPrices = vi.fn(async (_tickers?: string[], _years?: number) => ({
  start: "2025-04-26",
  end: "2026-05-31",
  ingested: [
    { ticker: "AAPL", prices: 252, dividends: 4, corporate_actions: 0 },
    { ticker: "MSFT", prices: 252, dividends: 4, corporate_actions: 1 },
  ],
  errors: [],
}));
const ingestFactors = vi.fn(async () => ({
  start: "2025-04-26",
  end: "2026-05-31",
  counts: { SMB: 252, HML: 252, MOM: 252 },
}));
const saveCredential = vi.fn(async (_provider: string, _body: { api_key: string }) => ({
  provider: "FRED",
  has_key: true,
  source: "stored" as const,
}));

vi.mock("../api/client", () => ({
  fetchConnections: () => Promise.resolve(rows),
  fetchCredentials: () =>
    Promise.resolve([{ provider: "FRED", has_key: false, source: "none" }]),
  saveCredential: (provider: string, body: { api_key: string }) =>
    saveCredential(provider, body),
  fetchSecuritiesHealth: () =>
    Promise.resolve({
      benchmark: "SPY",
      benchmark_bars: 252,
      securities_projected: 540,
      names_with_30plus_bars: 539,
      names_with_computable_beta: 539,
      factor_panel: "MKT-only",
      source: "db",
      reason: "ok",
    }),
  saveConnection: (role: string, body: unknown) => saveConnection(role, body),
  testConnection: (role: string) => testConnection(role),
  syncUniverse: () => syncUniverse(),
  ingestPrices: (tickers?: string[], years?: number) => ingestPrices(tickers, years),
  ingestFactors: () => ingestFactors(),
  ingestMacro: (startYear?: number) =>
    Promise.resolve({ ingested: { UST_10Y: 100 }, total: 100, series: 1, startYear }),
  fetchMacroCatalog: () =>
    Promise.resolve({
      series: [
        {
          series_id: "UST_10Y",
          name: "UST 10Y CMT yield",
          category: "RATES",
          series_class: "MARKET",
          frequency: "DAILY",
          units: "percent",
          source: "FRED",
          source_code: "DGS10",
          description: "",
          ingestable: true,
          points: 0,
          last_date: null,
        },
      ],
      total: 1,
    }),
}));

describe("Settings", () => {
  beforeEach(() => {
    saveConnection.mockClear();
    testConnection.mockClear();
    syncUniverse.mockClear();
    ingestPrices.mockClear();
    ingestFactors.mockClear();
    saveCredential.mockClear();
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

  it("backfills Ken French factors and reports the observation count", async () => {
    render(<Settings />);
    await screen.findByText(/Securities DB/);
    fireEvent.click(screen.getByText("Backfill factors"));
    await waitFor(() => expect(ingestFactors).toHaveBeenCalledOnce());
    // 252 × 3 factors.
    expect(
      await screen.findByText(/Ingested 756 factor observations/),
    ).toBeInTheDocument();
  });

  it("saves a FRED API key (write-only) and links to where to get one", async () => {
    render(<Settings />);
    await screen.findByText(/Data provider API keys/);
    // The how-to-get-one link is surfaced.
    expect(screen.getByText(/fredaccount.stlouisfed.org\/apikeys/)).toBeInTheDocument();
    const input = screen.getByLabelText("FRED-api-key");
    fireEvent.change(input, { target: { value: "mysecretkey" } });
    fireEvent.click(screen.getByText("Save key"));
    await waitFor(() => expect(saveCredential).toHaveBeenCalledOnce());
    const [provider, body] = saveCredential.mock.calls[0];
    expect(provider).toBe("FRED");
    expect(body.api_key).toBe("mysecretkey");
    expect(await screen.findByText(/Key saved/)).toBeInTheDocument();
  });
});

import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Settings } from "./Settings";
import type { ConnectionRow } from "../types";

// All four roles, as the backend's GET /settings/connections always returns them. Note the
// UNIVERSE row deliberately sits on a different port — CATO is its own server, which is the
// case the family grouping has to represent rather than flatten.
const rows: ConnectionRow[] = [
  { role: "PORTFOLIO", host: "localhost", port: 5432, database: "neptune_portfolios",
    username: "neptune", has_password: true, configured: true, bootstrap: true },
  { role: "SECURITIES", host: "localhost", port: 5432, database: "neptune_securities",
    username: "neptune", has_password: true, configured: true, bootstrap: false },
  { role: "MACRO", host: "localhost", port: 5432, database: "neptune_macro",
    username: "neptune", has_password: true, configured: true, bootstrap: false },
  { role: "UNIVERSE", host: "localhost", port: 5434, database: "cato_securities",
    username: "readonly", has_password: true, configured: true, bootstrap: false },
];

// PORTFOLIO's response carries extra fields the other three roles' saves don't (see
// api/main.py's PORTFOLIO branch) -- overridable per-test so the env_updated:false path
// (repointed live, but couldn't persist to .env) can be exercised.
let portfolioSaveExtra: { reconnected?: boolean; env_updated?: boolean } = {
  reconnected: true, env_updated: true,
};
const saveConnection = vi.fn(async (role: string, _body: unknown) => {
  const base = rows.find((r) => r.role === role) ?? rows[3];
  return role === "PORTFOLIO" ? { ...base, ...portfolioSaveExtra } : base;
});
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

const createPortfolio = vi.fn(
  async (body: { name: string; mandate?: string; lead_pm_ids?: string[] }) => ({
    id: "new-book",
    name: body.name,
    mandate: "LONG_SHORT" as const,
  }),
);
const deletePortfolio = vi.fn(async (id: string) => ({ deleted: id }));
let portfolioList: { id: string; name: string; mandate: "LONG_SHORT" | "LONG_ONLY"; lead_pm_ids?: string[] }[] = [];

vi.mock("../api/client", () => ({
  createPortfolio: (body: { name: string }) => createPortfolio(body),
  deletePortfolio: (id: string) => deletePortfolio(id),
  fetchPortfolios: () => Promise.resolve(portfolioList),
  fetchFirms: () => Promise.resolve([{ id: "IRIDIUM", name: "Iridium", is_internal: true }]),
  fetchPeople: () =>
    Promise.resolve([
      { id: "pm-iridium", firm_id: "IRIDIUM", name: "Lead PM", role: "PM", email: null, is_active: true },
    ]),
  fetchEntities: () =>
    Promise.resolve([{ id: "IRIDIUM-FUND", firm_id: "IRIDIUM", name: "Iridium Fund", base_currency: "USD" }]),
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
  getSectorSource: () => Promise.resolve({ scheme: "YAHOO", available: ["YAHOO", "SIC", "KENFRENCH_12"] }),
  setSectorSource: (scheme: string) => Promise.resolve({ scheme }),
}));

/** Settings is sidebar-navigated: only the active section is mounted, so a test has to click
 *  its way to the section it exercises before querying for anything inside it. */
function gotoSection(label: string) {
  fireEvent.click(screen.getByRole("button", { name: label }));
}

describe("Settings", () => {
  beforeEach(() => {
    saveConnection.mockClear();
    testConnection.mockClear();
    syncUniverse.mockClear();
    ingestPrices.mockClear();
    ingestFactors.mockClear();
    saveCredential.mockClear();
    createPortfolio.mockClear();
    deletePortfolio.mockClear();
    portfolioList = [];
    portfolioSaveExtra = { reconnected: true, env_updated: true };
  });

  it("groups the databases by family and flags the bootstrap DB", async () => {
    render(<Settings />);
    gotoSection("Databases");
    // Family headings, not four flat role cards.
    expect(await screen.findByText("Neptune databases")).toBeInTheDocument();
    expect(screen.getByText("CATO databases")).toBeInTheDocument();
    // Every member database is offered, including ones with no stored row yet.
    expect(screen.getByText("Portfolio (app)")).toBeInTheDocument();
    expect(screen.getByText("Securities (market data)")).toBeInTheDocument();
    expect(screen.getByText("Macro (rates, credit, economic)")).toBeInTheDocument();
    expect(screen.getByText("Universe (cato_securities)")).toBeInTheDocument();
    // The portfolio DB is flagged as the bootstrap; on the web path (jsdom has no
    // window.neptune) saving it now reconnects live rather than needing a restart.
    expect(screen.getByText(/bootstrap/)).toBeInTheDocument();
    expect(screen.getByText(/reconnects immediately/)).toBeInTheDocument();
  });

  it("navigates between sections from the rail", async () => {
    render(<Settings />);
    // General is the landing section; Databases content is not mounted yet.
    expect(screen.queryByText("Neptune databases")).not.toBeInTheDocument();
    gotoSection("Databases");
    expect(await screen.findByText("Neptune databases")).toBeInTheDocument();
    gotoSection("Portfolios");
    expect(screen.queryByText("Neptune databases")).not.toBeInTheDocument();
    expect(await screen.findByText(/No portfolios yet/)).toBeInTheDocument();
  });

  it("saves a connection with a blank password (preserves stored secret)", async () => {
    render(<Settings />);
    gotoSection("Databases");
    await screen.findByText("CATO databases");
    // Edit something other than the password, then save from the header.
    fireEvent.change(screen.getByLabelText("universe-database"), {
      target: { value: "cato_securities_v2" },
    });
    fireEvent.click(screen.getByLabelText("save-settings"));
    await waitFor(() => expect(saveConnection).toHaveBeenCalled());
    const call = saveConnection.mock.calls.find(([role]) => role === "UNIVERSE");
    expect(call).toBeDefined();
    const body = call![1] as { password: string | null; database: string };
    // Blank password is sent as null so the backend leaves the stored secret unchanged.
    expect(body.password).toBeNull();
    expect(body.database).toBe("cato_securities_v2");
  });

  it("fans a shared server out across every database in the family", async () => {
    render(<Settings />);
    gotoSection("Databases");
    await screen.findByText("Neptune databases");
    // One edit to the family server, not three edits to three cards.
    fireEvent.change(screen.getByLabelText("neptune-shared-host"), {
      target: { value: "pg-prod.internal" },
    });
    fireEvent.click(screen.getByLabelText("save-settings"));
    await waitFor(() => expect(saveConnection).toHaveBeenCalledTimes(3));
    const saved = Object.fromEntries(
      saveConnection.mock.calls.map(([role, body]) => [role, body as { host: string; database: string }]),
    );
    expect(Object.keys(saved).sort()).toEqual(["MACRO", "PORTFOLIO", "SECURITIES"]);
    // Shared credential, but each database keeps its own name.
    expect(saved.PORTFOLIO.host).toBe("pg-prod.internal");
    expect(saved.SECURITIES.host).toBe("pg-prod.internal");
    expect(saved.MACRO.host).toBe("pg-prod.internal");
    expect(saved.PORTFOLIO.database).toBe("neptune_portfolios");
    expect(saved.MACRO.database).toBe("neptune_macro");
    // CATO is a different family and is untouched.
    expect(saved.UNIVERSE).toBeUndefined();
  });

  it("warns when the portfolio DB reconnects live but the .env write fails", async () => {
    // The live swap already took effect (see api/main.py) even when env_updated is false —
    // this is a durability warning, not a failure to save.
    portfolioSaveExtra = { reconnected: true, env_updated: false };
    render(<Settings />);
    gotoSection("Databases");
    await screen.findByText("Neptune databases");
    fireEvent.change(screen.getByLabelText("neptune-shared-host"), {
      target: { value: "pg-prod.internal" },
    });
    fireEvent.click(screen.getByLabelText("save-settings"));
    await waitFor(() => expect(saveConnection).toHaveBeenCalledTimes(3));
    expect(
      await screen.findByText(/couldn.t save to \.env — this will revert on the next restart/),
    ).toBeInTheDocument();
  });

  it("disables Test connection on an edited row, since it tests the stored URL", async () => {
    render(<Settings />);
    gotoSection("Databases");
    await screen.findByText("CATO databases");
    expect(screen.getByLabelText("universe-test")).toBeEnabled();
    fireEvent.change(screen.getByLabelText("cato-shared-host"), {
      target: { value: "cato-prod.internal" },
    });
    expect(screen.getByLabelText("universe-test")).toBeDisabled();
  });

  it("loads the sector source and saves a change immediately (no Save button needed)", async () => {
    render(<Settings />);
    const select = await screen.findByLabelText("sector-source");
    expect((select as HTMLSelectElement).value).toBe("YAHOO");

    fireEvent.change(select, { target: { value: "KENFRENCH_12" } });
    await waitFor(() => expect((select as HTMLSelectElement).value).toBe("KENFRENCH_12"));
  });

  it("lists GICS/BICS as disabled options -- no CATO data behind them yet", async () => {
    render(<Settings />);
    const select = await screen.findByLabelText("sector-source");
    const gics = within(select).getByText(/^GICS/) as HTMLOptionElement;
    const bics = within(select).getByText(/^BICS/) as HTMLOptionElement;
    expect(gics.disabled).toBe(true);
    expect(bics.disabled).toBe(true);
  });

  it("warns before closing with unsaved changes", async () => {
    const onClose = vi.fn();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<Settings onClose={onClose} />);
    gotoSection("Databases");
    await screen.findByText("Neptune databases");
    fireEvent.change(screen.getByLabelText("neptune-shared-host"), {
      target: { value: "somewhere-else" },
    });
    fireEvent.click(screen.getByText("✕ Close"));
    expect(confirmSpy).toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled(); // the user declined
    confirmSpy.mockRestore();
  });

  it("closes without prompting when nothing is dirty", async () => {
    const onClose = vi.fn();
    const confirmSpy = vi.spyOn(window, "confirm");
    render(<Settings onClose={onClose} />);
    await screen.findByText("Settings");
    fireEvent.click(screen.getByText("✕ Close"));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("syncs the universe and reports the count", async () => {
    render(<Settings />);
    gotoSection("Data Ingest");
    fireEvent.click(await screen.findByText("Sync universe"));
    await waitFor(() => expect(syncUniverse).toHaveBeenCalledOnce());
    expect(await screen.findByText(/Synced 42 securities/)).toBeInTheDocument();
  });

  it("backfills prices and reports total bars across names", async () => {
    render(<Settings />);
    gotoSection("Data Ingest");
    fireEvent.click(await screen.findByText("Backfill prices"));
    await waitFor(() => expect(ingestPrices).toHaveBeenCalledOnce());
    // 252 + 252 bars across 2 names.
    expect(
      await screen.findByText(/Ingested 504 price bars across 2 names/),
    ).toBeInTheDocument();
  });

  it("backfills Ken French factors and reports the observation count", async () => {
    render(<Settings />);
    gotoSection("Data Ingest");
    fireEvent.click(await screen.findByText("Backfill factors"));
    await waitFor(() => expect(ingestFactors).toHaveBeenCalledOnce());
    // 252 × 3 factors.
    expect(
      await screen.findByText(/Ingested 756 factor observations/),
    ).toBeInTheDocument();
  });

  it("shows an empty-state and adds a portfolio with ownership fields", async () => {
    const onChanged = vi.fn();
    render(<Settings onPortfoliosChanged={onChanged} />);
    gotoSection("Portfolios");
    expect(await screen.findByText(/No portfolios yet/)).toBeInTheDocument();
    // Fill the full-ownership add form and submit.
    fireEvent.change(screen.getByLabelText("new-portfolio-name"), {
      target: { value: "Macro Alpha" },
    });
    fireEvent.change(screen.getByLabelText("new-portfolio-pm"), {
      target: { value: "pm-iridium" },
    });
    fireEvent.click(screen.getByText("Add portfolio"));
    await waitFor(() => expect(createPortfolio).toHaveBeenCalledOnce());
    const body = createPortfolio.mock.calls[0][0];
    expect(body.name).toBe("Macro Alpha");
    expect(body.lead_pm_ids).toEqual(["pm-iridium"]);
    expect(onChanged).toHaveBeenCalled(); // the app refreshes its switcher
  });

  it("lists a book and removes it after confirmation", async () => {
    portfolioList = [
      { id: "macro-alpha", name: "Macro Alpha", mandate: "LONG_SHORT", lead_pm_ids: ["pm-iridium"] },
    ];
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<Settings />);
    gotoSection("Portfolios");
    expect(await screen.findByText("Macro Alpha")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Remove"));
    await waitFor(() => expect(deletePortfolio).toHaveBeenCalledWith("macro-alpha"));
    confirmSpy.mockRestore();
  });

  it("saves a FRED API key (write-only) and links to where to get one", async () => {
    render(<Settings />);
    gotoSection("API Keys");
    await screen.findByText(/Data provider API keys/);
    // The how-to-get-one link is surfaced.
    expect(screen.getByText(/fredaccount.stlouisfed.org\/apikeys/)).toBeInTheDocument();
    const input = screen.getByLabelText("FRED-api-key");
    fireEvent.change(input, { target: { value: "mysecretkey" } });
    fireEvent.click(screen.getByLabelText("save-settings"));
    await waitFor(() => expect(saveCredential).toHaveBeenCalledOnce());
    const [provider, body] = saveCredential.mock.calls[0];
    expect(provider).toBe("FRED");
    expect(body.api_key).toBe("mysecretkey");
    expect(await screen.findByText(/Key saved/)).toBeInTheDocument();
  });
});

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  Frontier,
  HedgeProposal,
  PendingHedge,
  PositionRow,
  RiskSummary,
  StressReport,
} from "./types";
import {
  approveHedge,
  bookHedgeLegs,
  fetchFrontier,
  fetchHealth,
  fetchPortfolios,
  fetchPositions,
  fetchRisk,
  fetchStress,
  getPriceRefresh,
  proposeHedge,
  recordTransaction,
  refreshPrices,
  setPriceRefresh,
} from "./api/client";
import type { PortfolioMeta } from "./api/client";
import type { TradeAction, TransactionInput } from "./types";
import { Portfolio } from "./tabs/Portfolio";
import { Trade } from "./tabs/Trade";
import { RiskDashboard } from "./tabs/RiskDashboard";
import { Hedge } from "./tabs/Hedge";
import { BetaHistory } from "./tabs/BetaHistory";
import { Stress } from "./tabs/Stress";
import { Settings } from "./tabs/Settings";
import { TitleBar } from "./components/TitleBar";
import { TabBar } from "./components/TabBar";
import { PortfolioSidebar } from "./components/PortfolioSidebar";

const TABS = ["Positions", "Trade", "Risk", "Hedge", "Beta", "Stress", "Settings"] as const;
type Tab = (typeof TABS)[number];
// Settings lives behind the title-bar gear (suite convention), so it's excluded from the tab row.
const NAV_TABS = TABS.filter((t) => t !== "Settings");

// Virtual roll-up views; the backend resolves these ids to the right slice of books.
const CONSOLIDATED_ID = "__consolidated__"; // every book
const LONGSHORT_GROUP_ID = "__long_short__"; // hedged (long/short) books only
const LONGONLY_GROUP_ID = "__long_only__"; // long-only books only

export default function App() {
  const [tab, setTab] = useState<Tab>("Positions");
  // Where Settings' Close returns to — the tab the gear was pressed from.
  const [settingsReturnTab, setSettingsReturnTab] = useState<Tab>("Positions");

  function openSettings() {
    if (tab !== "Settings") setSettingsReturnTab(tab);
    setTab("Settings");
  }
  // The selected portfolio. Defaults to the Consolidated roll-up across every book.
  const [portfolioId, setPortfolioId] = useState<string>(CONSOLIDATED_ID);
  const [portfolios, setPortfolios] = useState<PortfolioMeta[]>([]);
  // The Hedge tab always targets a REAL book (you can't hedge the Consolidated roll-up).
  const [hedgePortfolioId, setHedgePortfolioId] = useState<string>("");
  const [summary, setSummary] = useState<RiskSummary | null>(null);
  const [positions, setPositions] = useState<PositionRow[]>([]);
  const [proposal, setProposal] = useState<HedgeProposal | null>(null);
  const [proposing, setProposing] = useState(false);
  const [frontier, setFrontier] = useState<Frontier | null>(null);
  const [frontierLoading, setFrontierLoading] = useState(false);
  const [stress, setStress] = useState<StressReport | null>(null);
  const [stressLoading, setStressLoading] = useState(false);
  const [trading, setTrading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Live pricing: the SERVER refreshes prices every `refreshMins` minutes (0 = off). The
  // dashboard reads/writes that interval and re-displays the book on the same cadence.
  const [refreshMins, setRefreshMins] = useState<number>(10);
  const [pricing, setPricing] = useState(false);
  const [lastPriced, setLastPriced] = useState<string | null>(null);
  // Backend liveness for the title-bar status pill (polled).
  const [healthy, setHealthy] = useState(true);
  // Whether the core data load (risk + positions) is succeeding. null = not yet attempted.
  const [dataOk, setDataOk] = useState<boolean | null>(null);
  // Test Mode (Electron only): throwaway SQLite + seeded demo data when no real DB is reachable.
  const [inTestMode, setInTestMode] = useState(false);
  const [switchingMode, setSwitchingMode] = useState(false);

  // Load the portfolio list (for the switcher). Reused after a book is added/removed in Settings.
  function reloadPortfolios() {
    return fetchPortfolios()
      .then((ps) => {
        setPortfolios(ps); // keep the Consolidated default; the user picks a book explicitly
        // The hedge book defaults to a real LONG/SHORT book (roll-ups and long-only can't be hedged).
        const firstLS = ps.find((p) => p.mandate === "LONG_SHORT");
        if (firstLS) setHedgePortfolioId((cur) => cur || firstLS.id);
      })
      .catch((e) => setError(String(e)));
  }

  // Load the portfolio list + the server refresh interval, once.
  useEffect(() => {
    reloadPortfolios();
    getPriceRefresh()
      .then(({ minutes }) => setRefreshMins(minutes))
      .catch(() => {});
  }, []);

  // Poll backend health; retry portfolio load whenever the sidecar comes back online.
  // Fast initial probes at 3 s and 7 s catch the Python startup race (sidecar needs a few
  // seconds to boot); the 20 s interval handles recoveries after "Save & restart backend".
  useEffect(() => {
    let alive = true;
    // Start as "was down" so the very first successful health check retries the portfolio
    // fetch — this covers the race where the mount-time load ran before the sidecar was ready.
    let sidecarWasDown = true;
    const check = () =>
      fetchHealth()
        .then(() => {
          if (!alive) return;
          setHealthy(true);
          if (sidecarWasDown) {
            sidecarWasDown = false;
            reloadPortfolios();
          }
        })
        .catch(() => {
          if (alive) {
            sidecarWasDown = true;
            setHealthy(false);
          }
        });
    check();
    const t1 = setTimeout(check, 3_000);
    const t2 = setTimeout(check, 7_000);
    const id = setInterval(check, 20_000);
    return () => {
      alive = false;
      clearTimeout(t1);
      clearTimeout(t2);
      clearInterval(id);
    };
  }, []);

  // A new hedge target invalidates any standing proposal/frontier from the previous book.
  useEffect(() => {
    setProposal(null);
    setFrontier(null);
  }, [hedgePortfolioId]);

  // Test Mode plumbing (Electron shell only): reflect the current mode on mount.
  useEffect(() => {
    window.neptune?.isTestMode?.().then(setInTestMode).catch(() => {});
  }, []);

  const canTestMode = typeof window !== "undefined" && Boolean(window.neptune?.startTestMode);

  // Relaunch the backend on SQLite + seeded demo data (toTest=true) or back on the configured
  // Postgres (false), wait for it to answer, then reload. Surfaced as a manual control in Settings
  // that appears when no database is reachable.
  async function switchMode(toTest: boolean) {
    setSwitchingMode(true);
    try {
      if (toTest) await window.neptune?.startTestMode?.();
      else await window.neptune?.stopTestMode?.();
      // The sidecar restarts on the same port; wait for it to answer before reloading.
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        try {
          await fetchHealth();
          break;
        } catch {
          /* still restarting */
        }
      }
      setInTestMode(toTest);
      setHealthy(true);
      await reloadPortfolios();
      await loadBook(portfolioId);
    } finally {
      setSwitchingMode(false);
    }
  }

  // Load a book's risk + positions. Tracks dataOk so the UI can tell "backend/DB is broken" from
  // "backend is fine but this book is empty" (a failed fetch → dataOk false → offer Test Mode).
  const loadBook = useCallback((id: string) => {
    return Promise.all([fetchRisk(id), fetchPositions(id)])
      .then(([r, p]) => {
        setSummary(r);
        setPositions(p);
        setError(null);
        setDataOk(true);
      })
      .catch((e) => {
        setError(String(e));
        setDataOk(false);
      });
  }, []);

  // (Re)load the selected book whenever it changes.
  useEffect(() => {
    loadBook(portfolioId);
  }, [portfolioId, loadBook]);

  // An approved-but-not-yet-booked hedge, handed to the Trade tab for review + booking.
  const [pendingHedge, setPendingHedge] = useState<PendingHedge | null>(null);

  // Approve the basket → reconcile it against the LIVE systematic book into delta legs (cover
  // the names being dropped/reduced, sell the names being added/increased, skip unchanged), then
  // hand those to the Trade tab for review/booking. So when a hedge is already on, you see the
  // buy-to-covers — not a misleading wall of sells.
  async function handleApproveHedge() {
    if (!proposal) return;
    // Current systematic shorts for the hedge book (cover price = the live mark).
    let current: Record<string, { shares: number; price: number }> = {};
    try {
      const pos = await fetchPositions(hedgePortfolioId);
      current = Object.fromEntries(
        pos
          .filter((p) => p.side === "SHORT" && p.short_type === "SYSTEMATIC" && p.notional !== 0)
          .map((p) => [
            p.ticker,
            { shares: p.quantity, price: p.price ?? (p.quantity ? p.notional / p.quantity : 0) },
          ]),
      );
    } catch (e) {
      setError(String(e));
      return;
    }
    const target = new Map(
      proposal.proposed_shorts
        .filter((s) => s.shares && s.price)
        .map((s) => [s.ticker, s]),
    );
    const tickers = new Set<string>([...Object.keys(current), ...target.keys()]);
    const legs: PendingHedge["shorts"] = [];
    for (const ticker of tickers) {
      const cur = current[ticker]?.shares ?? 0;
      const t = target.get(ticker);
      const tgt = (t?.shares as number) ?? 0;
      const delta = tgt - cur;
      if (Math.abs(delta) < 1) continue; // unchanged (sub-share) — no trade, no churn
      if (delta > 0) {
        // open or increase the short
        legs.push({
          ticker, action: "SELL", shares: delta, price: t!.price as number,
          sector: t!.sector ?? null, beta: t!.beta, notional: t!.notional,
          kind: cur > 0 ? "increase" : "open",
        });
      } else {
        // buy-to-cover the names dropped/reduced, at the live mark
        legs.push({
          ticker, action: "BUY", shares: -delta, price: current[ticker].price,
          sector: t?.sector ?? null, beta: t?.beta ?? 0, notional: t?.notional ?? 0,
          kind: tgt > 0 ? "reduce" : "cover",
        });
      }
    }
    setPendingHedge({ portfolioId: hedgePortfolioId, shorts: legs });
    setProposal(null);
    setTab("Trade");
  }

  function handleRejectHedge() {
    setProposal(null);
  }

  async function handlePropose(sectorLimit?: number, maxNames?: number, betaAddBudget?: number) {
    setProposing(true);
    setError(null);
    try {
      setProposal(await proposeHedge(hedgePortfolioId, sectorLimit, maxNames, betaAddBudget));
    } catch (e) {
      setError(String(e));
    } finally {
      setProposing(false);
    }
  }

  async function handleFrontier() {
    setFrontierLoading(true);
    setError(null);
    try {
      setFrontier(await fetchFrontier(hedgePortfolioId));
    } catch (e) {
      setError(String(e));
    } finally {
      setFrontierLoading(false);
    }
  }

  async function handleStress() {
    setStressLoading(true);
    setError(null);
    try {
      setStress(await fetchStress(portfolioId));
    } catch (e) {
      setError(String(e));
    } finally {
      setStressLoading(false);
    }
  }

  // After any trade, refresh both the book and the risk summary so beta/factors reflect it.
  async function refreshBook() {
    const [r, p] = await Promise.all([fetchRisk(portfolioId), fetchPositions(portfolioId)]);
    setSummary(r);
    setPositions(p);
  }

  // Submit one ticket into a specific book. A systematic row (from an approved hedge) books as a
  // systematic short (I-03); a normal row books a manual Buy/Sell. Throws on failure so the Trade
  // tab can flag the row; the batch driver refreshes the book once at the end.
  async function submitTrade(targetId: string, t: TransactionInput, systematic?: boolean) {
    setTrading(true);
    try {
      if (systematic) {
        // A single systematic row still goes through the atomic replace-and-book endpoint.
        await approveHedge(targetId, [{ ticker: t.ticker, shares: t.quantity, price: t.price }]);
      } else {
        await recordTransaction(targetId, t);
      }
    } finally {
      setTrading(false);
    }
  }

  // Book the delta-reconciliation legs exactly as shown in the grid: BUY = buy-to-cover a
  // systematic short (books realized P&L), SELL = open/increase one. WYSIWYG — what the desk
  // reviewed is what gets booked. Discretionary shorts are never touched (I-03/I-04).
  async function submitHedgeBatch(
    targetId: string,
    legs: { ticker: string; action: TradeAction; shares: number; price: number }[],
  ) {
    setTrading(true);
    try {
      await bookHedgeLegs(targetId, legs);
    } finally {
      setTrading(false);
    }
  }

  // Manual "Refresh now": pull latest prices immediately, then re-display.
  async function handleRefreshPrices() {
    setPricing(true);
    try {
      await refreshPrices(portfolioId);
      await refreshBook();
      setLastPriced(new Date().toLocaleTimeString());
    } catch (e) {
      setError(String(e));
    } finally {
      setPricing(false);
    }
  }

  // Change the SERVER refresh interval (persisted + reschedules the running job).
  async function changeRefreshMins(m: number) {
    setRefreshMins(m);
    try {
      await setPriceRefresh(m);
    } catch (e) {
      setError(String(e));
    }
  }

  // Re-display the book on the server's cadence so server-refreshed prices show up. A ref
  // holds the freshest reader so the interval resets only when the interval length changes.
  const displayRef = useRef(refreshBook);
  displayRef.current = refreshBook;
  useEffect(() => {
    if (!refreshMins || refreshMins <= 0) return; // 0 = off
    const id = setInterval(() => {
      displayRef.current().then(() => setLastPriced(new Date().toLocaleTimeString()));
    }, refreshMins * 60_000);
    return () => clearInterval(id);
  }, [refreshMins]);

  // Split books by mandate for the grouped switcher and to scope the Hedge tab (you can only
  // hedge a real long/short book — never a roll-up or a long-only book).
  const longShortBooks = portfolios.filter((p) => p.mandate === "LONG_SHORT");
  const longOnlyBooks = portfolios.filter((p) => p.mandate === "LONG_ONLY");

  // Display name for the selected book/roll-up (shown atop the Portfolio holdings pane).
  const selectedName =
    portfolioId === CONSOLIDATED_ID
      ? "Consolidated Positions"
      : portfolioId === LONGSHORT_GROUP_ID
        ? "Long / Short"
        : portfolioId === LONGONLY_GROUP_ID
          ? "Long Only"
          : (portfolios.find((p) => p.id === portfolioId)?.name ?? portfolioId);

  // The Hedge tab targets a single real Long/Short book — the last one selected (hedgePortfolioId).
  // Roll-ups and long-only books can't be hedged.
  const hedgeBook = longShortBooks.find((p) => p.id === hedgePortfolioId) ?? null;
  const canHedge = hedgeBook !== null;

  // Tabs with a background job in flight get a pulsing dot; the title-bar pill summarizes state.
  const runningTabs = new Set<string>();
  if (proposing || frontierLoading) runningTabs.add("Hedge");
  if (stressLoading) runningTabs.add("Stress");
  if (pricing) runningTabs.add("Positions");
  if (trading) runningTabs.add("Trade");
  const status: "ready" | "running" | "error" = !healthy
    ? "error"
    : runningTabs.size > 0
      ? "running"
      : "ready";

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <TitleBar
        status={status}
        testMode={inTestMode}
        onSettings={openSettings}
      />
      <TabBar tabs={NAV_TABS} active={tab} onChange={(t) => setTab(t as Tab)} running={runningTabs} />
      <div className="flex min-h-0 flex-1">
        {/* Persistent portfolios sidebar on every data tab. Selecting a book sets the global
            selection used by all tabs; it also points the Hedge tab at it when it's a real L/S
            book (roll-ups can't be hedged). Hidden on Settings (config, not a book view). */}
        {tab !== "Settings" && (
          <PortfolioSidebar
            longShortBooks={longShortBooks}
            longOnlyBooks={longOnlyBooks}
            // On Hedge the rail highlights the hedge target; elsewhere the global selection.
            selectedId={tab === "Hedge" ? hedgePortfolioId : portfolioId}
            onSelect={(id) => {
              // Selecting a real L/S book drives BOTH the global view and the hedge target;
              // a roll-up / long-only selection only moves the global view (hedge target sticks).
              setPortfolioId(id);
              if (longShortBooks.some((p) => p.id === id)) setHedgePortfolioId(id);
            }}
            consolidatedId={CONSOLIDATED_ID}
            longShortGroupId={LONGSHORT_GROUP_ID}
            longOnlyGroupId={LONGONLY_GROUP_ID}
            disabledReason={
              tab === "Hedge"
                ? (id) =>
                    longShortBooks.some((p) => p.id === id)
                      ? undefined
                      : id === CONSOLIDATED_ID ||
                          id === LONGSHORT_GROUP_ID ||
                          id === LONGONLY_GROUP_ID
                        ? "Roll-ups aren't a single tradeable book — pick a Long / Short book."
                        : "Long-only books hold intentional market beta — nothing to hedge."
                : undefined
            }
          />
        )}

        {/* Settings owns its own header + nav rail + scroll container, so it gets a flush,
            unpadded, non-scrolling frame; every other tab keeps the padded scroller. */}
        <main
          className={
            tab === "Settings"
              ? "flex min-w-0 flex-1 overflow-hidden"
              : "min-w-0 flex-1 overflow-auto px-6 py-6"
          }
        >
        {error && tab !== "Settings" && (
          <div className="mb-4 rounded border border-status-breach/40 bg-status-breach/10 p-3 text-sm text-status-breach">
            {error}
          </div>
        )}

        {/* Forced first portfolio: a fresh database has no books. Send the user to Settings to
            add one before the analytics tabs are meaningful. */}
        {portfolios.length === 0 && tab !== "Settings" && (
          <div className="mb-4 rounded-lg border border-ocean-accent/40 bg-ocean-accent/10 p-4">
            <p className="text-sm text-slate-200">
              No portfolios yet. Add your first book to start tracking risk and building the hedge.
            </p>
            <button
              onClick={openSettings}
              className="mt-3 rounded bg-ocean-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-ocean-accent/80"
            >
              Add a portfolio
            </button>
          </div>
        )}

        {/* Settings never depends on portfolio data — it's how you fix a bad DB target,
            so it must render even while the rest is still loading. */}
        {tab === "Settings" ? (
          <Settings
            onPortfoliosChanged={reloadPortfolios}
            onClose={() => setTab(settingsReturnTab)}
            canTestMode={canTestMode}
            inTestMode={inTestMode}
            dbReachable={dataOk}
            switchingMode={switchingMode}
            onStartTestMode={() => switchMode(true)}
            onStopTestMode={() => switchMode(false)}
            refreshMins={refreshMins}
            onChangeMins={changeRefreshMins}
            onRefreshNow={handleRefreshPrices}
            lastPriced={lastPriced}
            pricing={pricing}
          />
        ) : (
          <>
            {/* Only the Risk tab needs the loaded risk summary; every other tab renders its own
                content (and its own empty/error states). Gating just Risk — not the whole view —
                means switching tabs always changes the page even when the summary is still
                loading or the backend is erroring. */}
            {tab === "Risk" &&
              (summary ? (
                <RiskDashboard summary={summary} portfolioId={portfolioId} />
              ) : (
                <p className="text-ocean-muted">Loading risk summary…</p>
              ))}
            {tab === "Hedge" && (
              <Hedge
                proposal={proposal}
                canHedge={canHedge}
                portfolioName={hedgeBook?.name ?? null}
                onPropose={handlePropose}
                proposing={proposing}
                onApprove={handleApproveHedge}
                onReject={handleRejectHedge}
                approving={false}
                frontier={frontier}
                onFrontier={handleFrontier}
                frontierLoading={frontierLoading}
              />
            )}
            {tab === "Positions" && (
              <div className="space-y-4">
                <div>
                  <h2 className="font-display text-lg font-semibold text-white">{selectedName}</h2>
                  <p className="text-xs text-ocean-muted">
                    {positions.length} position{positions.length === 1 ? "" : "s"}
                  </p>
                </div>
                <Portfolio positions={positions} />
              </div>
            )}
            {tab === "Trade" && (
              <Trade
                portfolios={portfolios}
                defaultPortfolioId={portfolioId}
                onSubmit={submitTrade}
                onSubmitHedge={submitHedgeBatch}
                onAfterBatch={refreshBook}
                busy={trading}
                pendingHedge={pendingHedge}
                onConsumeHedge={() => setPendingHedge(null)}
              />
            )}
            {tab === "Beta" && <BetaHistory portfolioId={portfolioId} />}
            {tab === "Stress" && (
              <Stress report={stress} onRun={handleStress} loading={stressLoading} />
            )}
          </>
        )}
        </main>
      </div>
    </div>
  );
}

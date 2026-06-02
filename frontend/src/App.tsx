import { useEffect, useRef, useState } from "react";
import type {
  Frontier,
  HedgeProposal,
  PositionRow,
  RiskSummary,
  StressReport,
} from "./types";
import {
  approveHedge,
  fetchFrontier,
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
import type { TransactionInput } from "./types";
import { Portfolio } from "./tabs/Portfolio";
import { Trade } from "./tabs/Trade";
import { RiskDashboard } from "./tabs/RiskDashboard";
import { Hedge } from "./tabs/Hedge";
import { Stress } from "./tabs/Stress";
import { Settings } from "./tabs/Settings";

const TABS = ["Portfolio", "Trade", "Risk", "Hedge", "Stress", "Settings"] as const;
type Tab = (typeof TABS)[number];

// The virtual "all books" roll-up; the backend resolves this id to every book's positions.
const CONSOLIDATED_ID = "__consolidated__";

export default function App() {
  const [tab, setTab] = useState<Tab>("Portfolio");
  // The selected portfolio. Defaults to the Consolidated roll-up across every book.
  const [portfolioId, setPortfolioId] = useState<string>(CONSOLIDATED_ID);
  const [portfolios, setPortfolios] = useState<{ id: string; name: string }[]>([]);
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

  // Load the portfolio list (for the switcher) + the server refresh interval, once.
  useEffect(() => {
    fetchPortfolios()
      .then(setPortfolios) // keep the Consolidated default; the user picks a book explicitly
      .catch((e) => setError(String(e)));
    getPriceRefresh()
      .then(({ minutes }) => setRefreshMins(minutes))
      .catch(() => {});
  }, []);

  // (Re)load the selected book whenever it changes.
  useEffect(() => {
    Promise.all([fetchRisk(portfolioId), fetchPositions(portfolioId)])
      .then(([r, p]) => {
        setSummary(r);
        setPositions(p);
      })
      .catch((e) => setError(String(e)));
  }, [portfolioId]);

  const [approving, setApproving] = useState(false);

  // Approve the WHOLE basket → book the names as systematic shorts (I-03), then go to Trade.
  async function handleApproveHedge() {
    if (!proposal) return;
    setApproving(true);
    setError(null);
    try {
      const shorts = proposal.proposed_shorts
        .filter((s) => s.shares && s.price)
        .map((s) => ({ ticker: s.ticker, shares: s.shares as number, price: s.price as number }));
      await approveHedge(portfolioId, shorts);
      setProposal(null);
      await refreshBook();
      setTab("Trade");
    } catch (e) {
      setError(String(e));
    } finally {
      setApproving(false);
    }
  }

  function handleRejectHedge() {
    setProposal(null);
  }

  async function handlePropose(sectorLimit?: number, maxNames?: number) {
    setProposing(true);
    setError(null);
    try {
      setProposal(await proposeHedge(portfolioId, sectorLimit, maxNames));
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
      setFrontier(await fetchFrontier(portfolioId));
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

  // Submit one ticket into a specific book. Throws on failure so the Trade tab can flag the
  // individual ticket; the batch driver refreshes the book once at the end.
  async function submitTrade(targetId: string, t: TransactionInput) {
    setTrading(true);
    try {
      await recordTransaction(targetId, t);
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

  return (
    <div className="min-h-screen">
      <header className="border-b border-ocean-border bg-ocean-panel/60">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div>
            <h1 className="font-display text-xl font-semibold text-white">Neptune</h1>
            <p className="text-xs text-ocean-muted">Iridium Capital Management</p>
          </div>
          <nav className="flex gap-1">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`rounded px-3 py-1.5 text-sm font-medium transition ${
                  tab === t
                    ? "bg-ocean-accent text-white"
                    : "text-ocean-muted hover:text-slate-200"
                }`}
              >
                {t}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-8">
        {error && (
          <div className="mb-4 rounded border border-status-breach/40 bg-status-breach/10 p-3 text-sm text-status-breach">
            {error}
          </div>
        )}

        {/* Settings never depends on portfolio data — it's how you fix a bad DB target,
            so it must render even while the rest is still loading. */}
        {tab === "Settings" ? (
          <Settings />
        ) : !summary ? (
          <p className="text-ocean-muted">Loading…</p>
        ) : (
          <>
            {tab === "Risk" && <RiskDashboard summary={summary} />}
            {tab === "Hedge" && (
              <Hedge
                proposal={proposal}
                onPropose={handlePropose}
                proposing={proposing}
                onApplySectorLimit={(limit) => handlePropose(limit)}
                onApprove={handleApproveHedge}
                onReject={handleRejectHedge}
                approving={approving}
                frontier={frontier}
                onFrontier={handleFrontier}
                frontierLoading={frontierLoading}
              />
            )}
            {tab === "Portfolio" && (
              <div className="space-y-6">
                {/* The portfolio selector lives on the Portfolio page. Consolidated (all
                    books) is the default; the choice persists across the other tabs. */}
                <label className="flex items-center gap-2 text-xs text-ocean-muted">
                  Portfolio
                  <select
                    className="np-input py-1"
                    value={portfolioId}
                    onChange={(e) => setPortfolioId(e.target.value)}
                  >
                    <option value={CONSOLIDATED_ID} style={{ fontWeight: 700 }}>
                      Consolidated
                    </option>
                    {portfolios.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </label>
                <Portfolio
                  positions={positions}
                  refreshMins={refreshMins}
                  onChangeMins={changeRefreshMins}
                  onRefreshNow={handleRefreshPrices}
                  lastPriced={lastPriced}
                  pricing={pricing}
                />
              </div>
            )}
            {tab === "Trade" && (
              <Trade
                portfolios={portfolios}
                defaultPortfolioId={portfolioId}
                onSubmit={submitTrade}
                onAfterBatch={refreshBook}
                busy={trading}
              />
            )}
            {tab === "Stress" && (
              <Stress report={stress} onRun={handleStress} loading={stressLoading} />
            )}
          </>
        )}
      </main>
    </div>
  );
}

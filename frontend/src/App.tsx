import { useEffect, useRef, useState } from "react";
import type {
  Frontier,
  HedgeProposal,
  PositionRow,
  RiskSummary,
  StressReport,
} from "./types";
import {
  closePosition,
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
import { HedgeApproval } from "./tabs/HedgeApproval";
import { Stress } from "./tabs/Stress";
import { Settings } from "./tabs/Settings";

const TABS = ["Portfolio", "Trade", "Risk", "Hedge Approval", "Stress", "Settings"] as const;
type Tab = (typeof TABS)[number];

export default function App() {
  const [tab, setTab] = useState<Tab>("Portfolio");
  // The selected book. Multiple portfolios roll up into one firm book (Total Book view: TODO).
  const [portfolioId, setPortfolioId] = useState<string>("IRIDIUM-CORE");
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
      .then((ps) => {
        setPortfolios(ps);
        if (ps.length && !ps.some((p) => p.id === portfolioId)) setPortfolioId(ps[0].id);
      })
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

  async function handlePropose(sectorLimit?: number) {
    setProposing(true);
    setError(null);
    try {
      setProposal(await proposeHedge(portfolioId, sectorLimit));
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

  async function handleRecord(t: TransactionInput) {
    setTrading(true);
    setError(null);
    try {
      await recordTransaction(portfolioId, t);
      await refreshBook();
    } catch (e) {
      setError(String(e));
    } finally {
      setTrading(false);
    }
  }

  async function handleClose(positionId: number, quantity: number, exitPrice: number) {
    setTrading(true);
    setError(null);
    try {
      await closePosition(portfolioId, positionId, quantity, exitPrice);
      await refreshBook();
    } catch (e) {
      setError(String(e));
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
          <div className="flex items-center gap-4">
            <div>
              <h1 className="font-display text-xl font-semibold text-white">Neptune</h1>
              <p className="text-xs text-ocean-muted">Iridium Capital Management</p>
            </div>
            <label className="flex items-center gap-2 text-xs text-ocean-muted">
              Book
              <select
                className="np-input py-1"
                value={portfolioId}
                onChange={(e) => setPortfolioId(e.target.value)}
              >
                {portfolios.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
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
            {tab === "Risk" && (
              <RiskDashboard
                summary={summary}
                proposal={proposal}
                onPropose={handlePropose}
                proposing={proposing}
                onApplySectorLimit={(limit) => handlePropose(limit)}
                frontier={frontier}
                onFrontier={handleFrontier}
                frontierLoading={frontierLoading}
              />
            )}
            {tab === "Portfolio" && (
              <Portfolio
                positions={positions}
                refreshMins={refreshMins}
                onChangeMins={changeRefreshMins}
                onRefreshNow={handleRefreshPrices}
                lastPriced={lastPriced}
                pricing={pricing}
              />
            )}
            {tab === "Trade" && (
              <Trade
                positions={positions}
                onRecord={handleRecord}
                onClose={handleClose}
                busy={trading}
              />
            )}
            {tab === "Hedge Approval" && <HedgeApproval proposal={proposal} />}
            {tab === "Stress" && (
              <Stress report={stress} onRun={handleStress} loading={stressLoading} />
            )}
          </>
        )}
      </main>
    </div>
  );
}

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
  fetchPositions,
  fetchRisk,
  fetchStress,
  proposeHedge,
  recordTransaction,
  refreshPrices,
} from "./api/client";
import type { TransactionInput } from "./types";
import { Blotter } from "./tabs/Blotter";
import { Trade } from "./tabs/Trade";
import { RiskDashboard } from "./tabs/RiskDashboard";
import { HedgeApproval } from "./tabs/HedgeApproval";
import { Stress } from "./tabs/Stress";
import { Settings } from "./tabs/Settings";

const PORTFOLIO_ID = "IRIDIUM-CORE";
const TABS = ["Portfolio", "Trade", "Risk", "Hedge Approval", "Stress", "Settings"] as const;
type Tab = (typeof TABS)[number];

export default function App() {
  const [tab, setTab] = useState<Tab>("Risk");
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
  // Live pricing: poll /refresh-prices every `refreshMins` minutes (0 = off), persisted.
  const [refreshMins, setRefreshMins] = useState<number>(
    () => Number(localStorage.getItem("np_refresh_mins")) || 10,
  );
  const [pricing, setPricing] = useState(false);
  const [lastPriced, setLastPriced] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([fetchRisk(PORTFOLIO_ID), fetchPositions(PORTFOLIO_ID)])
      .then(([r, p]) => {
        setSummary(r);
        setPositions(p);
      })
      .catch((e) => setError(String(e)));
  }, []);

  async function handlePropose(sectorLimit?: number) {
    setProposing(true);
    setError(null);
    try {
      setProposal(await proposeHedge(PORTFOLIO_ID, sectorLimit));
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
      setFrontier(await fetchFrontier(PORTFOLIO_ID));
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
      setStress(await fetchStress(PORTFOLIO_ID));
    } catch (e) {
      setError(String(e));
    } finally {
      setStressLoading(false);
    }
  }

  // After any trade, refresh both the book and the risk summary so beta/factors reflect it.
  async function refreshBook() {
    const [r, p] = await Promise.all([fetchRisk(PORTFOLIO_ID), fetchPositions(PORTFOLIO_ID)]);
    setSummary(r);
    setPositions(p);
  }

  async function handleRecord(t: TransactionInput) {
    setTrading(true);
    setError(null);
    try {
      await recordTransaction(PORTFOLIO_ID, t);
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
      await closePosition(PORTFOLIO_ID, positionId, quantity, exitPrice);
      await refreshBook();
    } catch (e) {
      setError(String(e));
    } finally {
      setTrading(false);
    }
  }

  async function handleRefreshPrices() {
    setPricing(true);
    try {
      await refreshPrices(PORTFOLIO_ID);
      await refreshBook();
      setLastPriced(new Date().toLocaleTimeString());
    } catch (e) {
      setError(String(e));
    } finally {
      setPricing(false);
    }
  }

  function changeRefreshMins(m: number) {
    setRefreshMins(m);
    localStorage.setItem("np_refresh_mins", String(m));
  }

  // Auto-poll latest prices. A ref holds the freshest handler so the interval isn't reset
  // on every render — only when the interval length changes.
  const refreshRef = useRef(handleRefreshPrices);
  refreshRef.current = handleRefreshPrices;
  useEffect(() => {
    if (!refreshMins || refreshMins <= 0) return; // 0 = off
    const id = setInterval(() => refreshRef.current(), refreshMins * 60_000);
    return () => clearInterval(id);
  }, [refreshMins]);

  return (
    <div className="min-h-screen">
      <header className="border-b border-ocean-border bg-ocean-panel/60">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div>
            <h1 className="font-display text-xl font-semibold text-white">Neptune</h1>
            <p className="text-xs text-ocean-muted">
              Iridium Capital Management &middot; {PORTFOLIO_ID}
            </p>
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
              <Blotter
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

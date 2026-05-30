import { useEffect, useState } from "react";
import type {
  Frontier,
  HedgeProposal,
  PositionRow,
  RiskSummary,
  StressReport,
} from "./types";
import {
  fetchFrontier,
  fetchPositions,
  fetchRisk,
  fetchStress,
  proposeHedge,
} from "./api/client";
import { Blotter } from "./tabs/Blotter";
import { RiskDashboard } from "./tabs/RiskDashboard";
import { HedgeApproval } from "./tabs/HedgeApproval";
import { Stress } from "./tabs/Stress";

const PORTFOLIO_ID = "IRIDIUM-CORE";
const TABS = ["Blotter", "Risk", "Hedge Approval", "Stress"] as const;
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
  const [error, setError] = useState<string | null>(null);

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

        {!summary ? (
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
            {tab === "Blotter" && <Blotter positions={positions} />}
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

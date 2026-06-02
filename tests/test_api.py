"""End-to-end slice test through the FastAPI app (TestClient)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from neptune.api.main import app

PID = "IRIDIUM-CORE"  # seeded golden portfolio


@pytest.fixture()
def client():
    with TestClient(app) as c:  # triggers lifespan: init_db + seed golden portfolio
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["beta_tol"] == 0.05


def test_hedge_diagnostics_reports_source(client):
    # The seeded golden book has no stored prices, so it must report the synthetic fallback
    # with a human reason — never a bare empty universe.
    d = client.get(f"/portfolios/{PID}/hedge/diagnostics").json()
    assert d["source"] == "synthetic"
    assert "reason" in d and d["reason"]
    assert d["names_with_30plus_bars"] == 0  # nothing backfilled in the test DB


def test_consolidated_rolls_up_all_books(client):
    # A second book with a position; the consolidated view spans both books' positions,
    # and risk computes on the combined book. Trading into consolidated is rejected.
    client.post("/portfolios", json={"id": "BOOK-X", "name": "Book X"})
    client.post("/portfolios/BOOK-X/positions", json={
        "ticker": "ZZZ", "side": "LONG", "notional": 100000.0,
    })
    cons = client.get("/portfolios/__consolidated__/positions").json()
    tickers = {p["ticker"] for p in cons}
    assert "ZZZ" in tickers  # spans the new book
    assert client.get("/portfolios/__consolidated__/risk").status_code == 200
    # Consolidated is read-only: you must pick a real book to trade.
    r = client.post("/portfolios/__consolidated__/positions", json={
        "ticker": "QQQ", "side": "LONG", "notional": 1000.0,
    })
    assert r.status_code == 409


def test_mandate_rollups_and_long_only_guards(client):
    # Create a long-only book and a hedged book; the switcher reports mandate per book.
    client.post("/portfolios", json={"id": "LO-1", "name": "Long Only Fund", "mandate": "LONG_ONLY"})
    client.post("/portfolios/LO-1/positions", json={
        "ticker": "LOLONG", "side": "LONG", "notional": 2_000_000.0,
    })
    by_id = {p["id"]: p for p in client.get("/portfolios").json()}
    assert by_id["LO-1"]["mandate"] == "LONG_ONLY"
    assert by_id[PID]["mandate"] == "LONG_SHORT"  # seeded book defaults to hedged

    # The Long Only roll-up spans only long-only books and is reported as informational
    # (no neutrality required), so its net long beta is not a breach.
    lo = client.get("/portfolios/__long_only__/risk").json()
    assert lo["mandate"] == "LONG_ONLY"
    assert lo["beta_neutral_required"] is False
    assert lo["beta_status"] == "INFO"
    assert "LOLONG" in {p["ticker"] for p in client.get("/portfolios/__long_only__/positions").json()}

    # The Long/Short roll-up excludes the long-only book.
    ls_tickers = {p["ticker"] for p in client.get("/portfolios/__long_short__/positions").json()}
    assert "LOLONG" not in ls_tickers

    # A long-only book cannot be shorted or hedged.
    assert client.post("/portfolios/LO-1/positions", json={
        "ticker": "X", "side": "SHORT", "notional": 100.0, "short_type": "DISCRETIONARY",
    }).status_code in (409, 422)
    assert client.post("/portfolios/LO-1/hedge/propose").status_code == 422


def test_list_and_create_portfolios(client):
    # The seeded golden book is listed.
    ids = {p["id"] for p in client.get("/portfolios").json()}
    assert PID in ids
    # Create a second book; it shows up; duplicates are rejected.
    r = client.post("/portfolios", json={"id": "BOOK-2", "name": "Second Book"})
    assert r.status_code == 201
    assert "BOOK-2" in {p["id"] for p in client.get("/portfolios").json()}
    assert client.post("/portfolios", json={"id": "BOOK-2", "name": "dup"}).status_code == 409


def test_seeded_risk_summary_shows_net_beta(client):
    r = client.get(f"/portfolios/{PID}/risk")
    assert r.status_code == 200
    body = r.json()
    assert body["net_beta"] == pytest.approx(0.94, abs=1e-6)
    assert body["beta_neutral"] is False
    # Market is the beta gauge; the factor table shows the style factors (FF5 + Momentum).
    assert {f["factor"] for f in body["factors"]} == {"SMB", "HML", "RMW", "CMA", "MOM"}
    # Factor exposures are now real (computed from the regression), not zero placeholders.
    assert any(abs(f["exposure"]) > 1e-6 for f in body["factors"])


def test_hedge_proposal_is_constrained_and_pending(client):
    r = client.post(f"/portfolios/{PID}/hedge/propose")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "PENDING_APPROVAL"
    assert body["net_beta_before"] == pytest.approx(0.94, abs=1e-6)
    assert abs(body["net_beta_after"]) <= 0.05 + 1e-6
    assert len(body["proposed_shorts"]) > 0
    # Sector concentration breakdown is present, sums to ~1, and uses the default limit.
    assert body["sector_limit"] == 0.30
    assert sum(s["fraction"] for s in body["sectors"]) == pytest.approx(1.0, abs=1e-6)
    assert all("sector" in p for p in body["proposed_shorts"])


def test_hedge_proposal_enforces_sector_limit(client):
    loose = client.post(f"/portfolios/{PID}/hedge/propose?sector_limit=0.9").json()
    tight = client.post(f"/portfolios/{PID}/hedge/propose?sector_limit=0.2").json()
    assert tight["sector_limit"] == 0.2
    # The cap is a HARD constraint: the proposed hedge never breaches it.
    assert tight["sector_breaches"] == []
    assert all(s["fraction"] <= 0.2 + 1e-6 for s in tight["sectors"])
    # A tighter cap forces a more diversified short book than a loose one.
    assert max(s["fraction"] for s in tight["sectors"]) <= \
        max(s["fraction"] for s in loose["sectors"]) + 1e-9


def test_hedge_proposal_sector_cap_too_tight_fails_closed(client):
    # 6 sectors can't each stay under ~10%, so no compliant hedge exists → fail closed (422),
    # never a breaching proposal.
    r = client.post(f"/portfolios/{PID}/hedge/propose?sector_limit=0.1")
    assert r.status_code == 422
    assert "sector cap" in r.json()["detail"]


def test_propose_rejects_out_of_range_sector_limit(client):
    assert client.post(f"/portfolios/{PID}/hedge/propose?sector_limit=1.5").status_code == 422
    assert client.post(f"/portfolios/{PID}/hedge/propose?sector_limit=0").status_code == 422


def test_propose_on_unknown_portfolio_404(client):
    assert client.post("/portfolios/NEWBOOK/hedge/propose").status_code == 404


def test_hedge_frontier_returns_capped_runs(client):
    r = client.post(f"/portfolios/{PID}/hedge/frontier")
    assert r.status_code == 200
    body = r.json()
    caps = [pt["n_cap"] for pt in body["frontier"]]
    # Adaptive caps straddle the natural support: ascending, distinct, non-degenerate.
    assert caps == sorted(caps)
    assert len(set(caps)) == len(caps)
    assert caps[0] < caps[-1]
    for pt in body["frontier"]:
        assert pt["n_selected"] <= pt["n_cap"]
        assert "tracking_error" in pt and "beta_within_tol" in pt
    # The trade-off is visible: tracking error is non-increasing in the cap, and the
    # loosest cap achieves beta neutrality.
    tes = [pt["tracking_error"] for pt in body["frontier"]]
    assert tes == sorted(tes, reverse=True)
    assert body["frontier"][-1]["beta_within_tol"] is True


def test_enter_position_and_list(client):
    r = client.post(
        f"/portfolios/{PID}/positions",
        json={"ticker": "EEE", "side": "LONG", "notional": 250000, "forward_beta": 1.1},
    )
    assert r.status_code == 201
    listing = client.get(f"/portfolios/{PID}/positions").json()
    assert any(p["ticker"] == "EEE" for p in listing)


def test_long_short_conflict_returns_409(client):
    r = client.post(
        f"/portfolios/{PID}/positions",
        json={"ticker": "AAA", "side": "SHORT", "notional": 100000,
              "short_type": "DISCRETIONARY"},
    )
    assert r.status_code == 409


def test_unknown_portfolio_404(client):
    assert client.get("/portfolios/DOES-NOT-EXIST/risk").status_code == 404


# --- P&L engine endpoints --------------------------------------------------------

def test_positions_carry_pm_attribution(client):
    """The seeded golden book has a lead PM; names with no override inherit it."""
    listing = client.get(f"/portfolios/{PID}/positions").json()
    aaa = next(p for p in listing if p["ticker"] == "AAA")
    assert aaa["pm_id"] == "pm-iridium"  # falls back to the book's lead PM
    assert "analyst_id" in aaa


def test_position_pm_override_is_surfaced(client):
    # pm_id/analyst_id are FKs to people, so create the firm staff first (FK enforced on
    # both SQLite and Postgres). The seed already created firm "IRIDIUM".
    client.post("/people", json={"id": "pm-override", "firm_id": "IRIDIUM",
                                 "name": "Override PM", "role": "PM"})
    client.post("/people", json={"id": "an-x", "firm_id": "IRIDIUM",
                                 "name": "Analyst X", "role": "ANALYST"})
    client.post(
        f"/portfolios/{PID}/positions",
        json={"ticker": "HHH", "side": "LONG", "notional": 100000, "forward_beta": 1.0,
              "pm_id": "pm-override", "analyst_id": "an-x"},
    )
    listing = client.get(f"/portfolios/{PID}/positions").json()
    hhh = next(p for p in listing if p["ticker"] == "HHH")
    assert hhh["pm_id"] == "pm-override"
    assert hhh["analyst_id"] == "an-x"


def test_positions_include_pnl_and_book(client):
    listing = client.get(f"/portfolios/{PID}/positions").json()
    aaa = next(p for p in listing if p["ticker"] == "AAA")
    assert aaa["book"] == "LONG"
    assert aaa["cost_basis_method"] == "FIFO"
    # Seeded lots are marked against the synthetic close, so unrealized P&L is nonzero.
    assert "pnl" in aaa and {"day", "total", "unrealized", "realized"} <= aaa["pnl"].keys()
    assert any(abs(p["pnl"]["unrealized"]) > 0 for p in listing)


def test_portfolio_pnl_splits_by_book(client):
    body = client.get(f"/portfolios/{PID}/pnl").json()
    assert {"day", "total", "unrealized", "realized"} <= body["total"].keys()
    # The three books are reported separately (invariant I-03 — never conflated).
    assert set(body["by_book"]) == {"LONG", "SYSTEMATIC_SHORT", "DISCRETIONARY_SHORT"}
    # Total equals the sum across books (within float tolerance).
    s = sum(b["total"] for b in body["by_book"].values())
    assert body["total"]["total"] == pytest.approx(s, abs=1e-6)
    # total == unrealized + realized at the book level.
    assert body["total"]["total"] == pytest.approx(
        body["total"]["unrealized"] + body["total"]["realized"], abs=1e-6
    )


def test_reduce_position_realises_pnl(client):
    # Open a fresh long with a known lot, then sell half above cost.
    add = client.post(
        f"/portfolios/{PID}/positions",
        json={
            "ticker": "FFF", "side": "LONG", "notional": 100000, "forward_beta": 1.0,
            "cost_basis_method": "FIFO",
            "lots": [{"quantity": 1000, "entry_price": 100.0, "entry_date": "2026-01-02"}],
        },
    )
    pos_id = add.json()["id"]
    r = client.post(
        f"/portfolios/{PID}/positions/{pos_id}/reduce",
        json={"quantity": 500, "exit_price": 110.0},
    )
    assert r.status_code == 200
    # 500 * (110 - 100) = 5000 realized.
    assert r.json()["realized_pnl"] == pytest.approx(5000.0)


def test_reduce_more_than_open_returns_422(client):
    add = client.post(
        f"/portfolios/{PID}/positions",
        json={
            "ticker": "GGG", "side": "LONG", "notional": 10000, "forward_beta": 1.0,
            "lots": [{"quantity": 100, "entry_price": 100.0, "entry_date": "2026-01-02"}],
        },
    )
    pos_id = add.json()["id"]
    r = client.post(
        f"/portfolios/{PID}/positions/{pos_id}/reduce",
        json={"quantity": 500, "exit_price": 110.0},
    )
    assert r.status_code == 422


# --- Stress engine endpoint ------------------------------------------------------

def test_stress_runs_standard_scenarios_and_var(client):
    r = client.post(f"/portfolios/{PID}/stress")
    assert r.status_code == 200
    body = r.json()
    names = [s["name"] for s in body["scenarios"]]
    assert "Market -10%" in names and "Market +10%" in names
    for s in body["scenarios"]:
        # Each scenario reports the three books separately (invariant I-03).
        assert set(s["by_book"]) <= {"LONG", "SYSTEMATIC_SHORT", "DISCRETIONARY_SHORT"}
        assert s["total_pnl"] == pytest.approx(sum(s["by_book"].values()), abs=1e-6)
    # VaR/ES are positive potential losses; ES >= VaR.
    var = body["var"]
    assert var["var"] > 0 and var["expected_shortfall"] >= var["var"]
    assert var["confidence"] == 0.95 and var["horizon_days"] == 1


def test_stress_long_book_loses_when_market_falls(client):
    body = client.post(f"/portfolios/{PID}/stress").json()
    down = next(s for s in body["scenarios"] if s["name"] == "Market -10%")
    # The golden book is net long beta, so a market drop is a loss overall.
    assert down["total_pnl"] < 0
    assert down["by_book"]["LONG"] < 0


def test_stress_accepts_custom_scenario_and_var_params(client):
    r = client.post(
        f"/portfolios/{PID}/stress",
        json={
            "scenarios": [{"name": "Crash -30%", "market_shock": -0.30}],
            "confidence": 0.99, "horizon_days": 10,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert any(s["name"] == "Crash -30%" for s in body["scenarios"])
    assert body["var"]["confidence"] == 0.99 and body["var"]["horizon_days"] == 10


def test_stress_rejects_bad_confidence(client):
    r = client.post(f"/portfolios/{PID}/stress", json={"confidence": 1.5})
    assert r.status_code == 422


def test_stress_returns_three_var_methods(client):
    body = client.post(f"/portfolios/{PID}/stress").json()
    methods = [m["method"] for m in body["var_methods"]]
    assert methods == ["parametric", "historical", "monte_carlo"]
    for m in body["var_methods"]:
        assert m["var"] > 0 and m["expected_shortfall"] >= m["var"]
    # Historical & Monte-Carlo carry an observation count; parametric does not.
    by = {m["method"]: m for m in body["var_methods"]}
    assert by["historical"]["n_observations"] > 0
    assert by["monte_carlo"]["n_observations"] > 0
    assert by["parametric"]["n_observations"] == 0
    # Back-compat: top-level `var` is the parametric result.
    assert body["var"]["method"] == "parametric"


def test_hedge_approve_books_systematic_shorts(client):
    # Approving a basket books each name as a SYSTEMATIC short (never discretionary — I-03),
    # recorded, never routed (I-01).
    r = client.post(f"/portfolios/{PID}/hedge/approve", json={
        "shorts": [{"ticker": "HDGX", "shares": 1000, "price": 50.0}],
    })
    assert r.status_code == 201
    assert r.json()["booked"] == 1
    shorts = [p for p in client.get(f"/portfolios/{PID}/positions").json()
              if p["ticker"] == "HDGX"]
    assert shorts and shorts[0]["side"] == "SHORT"
    assert shorts[0]["short_type"] == "SYSTEMATIC"
    # Consolidated is read-only — can't approve into it.
    assert client.post("/portfolios/__consolidated__/hedge/approve",
                       json={"shorts": []}).status_code == 409

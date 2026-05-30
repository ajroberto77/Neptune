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


def test_seeded_risk_summary_shows_net_beta(client):
    r = client.get(f"/portfolios/{PID}/risk")
    assert r.status_code == 200
    body = r.json()
    assert body["net_beta"] == pytest.approx(0.94, abs=1e-6)
    assert body["beta_neutral"] is False
    # Market is the beta gauge; the factor table shows the style factors.
    assert {f["factor"] for f in body["factors"]} == {"SMB", "HML", "MOM"}
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


def test_hedge_proposal_sector_limit_is_customizable(client):
    loose = client.post(f"/portfolios/{PID}/hedge/propose?sector_limit=0.9").json()
    tight = client.post(f"/portfolios/{PID}/hedge/propose?sector_limit=0.1").json()
    assert loose["sector_limit"] == 0.9
    assert tight["sector_limit"] == 0.1
    # A tighter limit flags at least as many sectors as a looser one (soft warning only;
    # the hedge itself is unchanged).
    assert len(tight["sector_breaches"]) >= len(loose["sector_breaches"])
    assert len(tight["sector_breaches"]) > 0
    assert [p["ticker"] for p in loose["proposed_shorts"]] == \
        [p["ticker"] for p in tight["proposed_shorts"]]


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

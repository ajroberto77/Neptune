"""Hedge optimizer tests, anchored on the golden portfolio and the |net beta| <= 0.05
hard constraint."""
from __future__ import annotations

import numpy as np
import pytest

from neptune.data.fixtures import GOLDEN_PORTFOLIO, golden_candidates, golden_positions
from neptune.domain.models import Portfolio
from neptune.quant.optimizer import (
    Candidate,
    InfeasibleHedge,
    ProposedShort,
    compute_residual,
    complexity_frontier,
    optimize_hedge,
    optimize_hedge_capped,
    sector_concentration,
)
from neptune.risk import book

BETA_TOL = 0.05


def _golden_portfolio() -> Portfolio:
    return Portfolio(id="G", name="golden", positions=golden_positions())


def test_residual_matches_golden_numbers():
    portfolio = _golden_portfolio()
    residual_beta, _ = compute_residual(book.residual_inputs(portfolio), portfolio.long_aum)
    assert residual_beta == pytest.approx(GOLDEN_PORTFOLIO["expected_net_beta_before"], abs=1e-9)
    assert book.net_beta(portfolio) == pytest.approx(0.94, abs=1e-9)


def test_optimizer_drives_net_beta_within_tolerance():
    portfolio = _golden_portfolio()
    residual_beta, residual_factors = compute_residual(
        book.residual_inputs(portfolio), portfolio.long_aum
    )

    proposal = optimize_hedge(
        residual_beta=residual_beta,
        residual_factors=residual_factors,
        universe=golden_candidates(),
        long_aum=portfolio.long_aum,
        beta_tol=BETA_TOL,
        max_position_weight=GOLDEN_PORTFOLIO["max_position_weight"],
        excluded_tickers={p.ticker for p in portfolio.longs},
    )

    # The HARD invariant.
    assert abs(proposal.net_beta_after) <= BETA_TOL + 1e-6
    # It actually did the work (started far outside tolerance).
    assert proposal.net_beta_before == pytest.approx(0.94, abs=1e-9)
    assert abs(proposal.net_beta_before) > BETA_TOL
    # It is a proposal, never an execution.
    assert proposal.status == "PENDING_APPROVAL"


def test_proposal_respects_size_ceiling_and_exclusions():
    portfolio = _golden_portfolio()
    residual_beta, residual_factors = compute_residual(
        book.residual_inputs(portfolio), portfolio.long_aum
    )
    long_tickers = {p.ticker for p in portfolio.longs}
    proposal = optimize_hedge(
        residual_beta, residual_factors, golden_candidates(), portfolio.long_aum,
        beta_tol=BETA_TOL, max_position_weight=0.15, excluded_tickers=long_tickers,
    )
    ceiling = 0.15 * portfolio.long_aum
    for short in proposal.positions:
        assert short.notional > 0
        # Sparse hedges concentrate, so a name can sit right on the 15% ceiling; allow a
        # solver-precision relative slack (the weight-space violation is ~1e-11).
        assert short.weight <= 0.15 + 1e-6
        assert short.notional <= ceiling * (1 + 1e-6)
        assert short.ticker not in long_tickers


def test_factor_limit_is_respected():
    # A residual with a large SMB tilt; hedges carry SMB loadings to neutralize it.
    universe = [
        Candidate("S1", beta=1.0, loadings={"SMB": 0.6}),
        Candidate("S2", beta=1.0, loadings={"SMB": 0.4}),
        Candidate("S3", beta=1.0, loadings={"SMB": 0.5}),
    ]
    residual_beta = 0.30
    residual_factors = {"SMB": 0.18, "HML": 0.0, "MOM": 0.0}
    proposal = optimize_hedge(
        residual_beta, residual_factors, universe, long_aum=1_000_000.0,
        beta_tol=BETA_TOL, factor_limit=0.20, max_position_weight=0.5,
    )
    assert abs(proposal.net_beta_after) <= BETA_TOL + 1e-6
    assert abs(proposal.factor_after["SMB"]) <= 0.20 + 1e-6


def test_hedge_is_sparse_not_a_dense_universe_basket():
    """A small residual over a wide universe must produce a PARSIMONIOUS basket — a handful of
    names that reproduce the exposure — not a tiny short in every name (the L1 gross penalty)."""
    universe = _wide_universe(n=200, seed=3)
    proposal = optimize_hedge(
        residual_beta=0.12, residual_factors={"SMB": 0.0, "HML": 0.0, "MOM": 0.0},
        universe=universe, long_aum=1_000_000.0, beta_tol=BETA_TOL, max_position_weight=0.15,
    )
    assert abs(proposal.net_beta_after) <= BETA_TOL + 1e-6  # still neutral
    assert proposal.n_selected <= 5  # not 200 — the whole point


def test_infeasible_hedge_fails_closed():
    # A huge residual beta that a low-beta, size-capped universe cannot neutralize.
    universe = [Candidate("S1", beta=0.5), Candidate("S2", beta=0.4)]
    with pytest.raises(InfeasibleHedge):
        optimize_hedge(
            residual_beta=5.0,
            residual_factors={"SMB": 0.0, "HML": 0.0, "MOM": 0.0},
            universe=universe,
            long_aum=1_000_000.0,
            beta_tol=BETA_TOL,
            max_position_weight=0.15,
        )


def test_empty_universe_rejected():
    with pytest.raises(ValueError):
        optimize_hedge(0.5, {}, [], long_aum=1_000_000.0)


def _wide_universe(n=60, seed=0):
    rng = np.random.default_rng(seed)
    return [
        Candidate(
            f"U{i}",
            beta=float(np.clip(rng.normal(1.0, 0.25), 0.4, 1.8)),
            loadings={f: float(rng.normal(0, 0.06)) for f in ("SMB", "HML", "MOM")},
        )
        for i in range(n)
    ]


def test_capped_run_limits_position_count():
    proposal = optimize_hedge_capped(
        residual_beta=0.6, residual_factors={"SMB": 0.05, "HML": 0.0, "MOM": 0.0},
        universe=_wide_universe(), long_aum=2_500_000.0, n_cap=5,
    )
    assert proposal.n_selected <= 5
    assert proposal.n_cap == 5


def test_capped_run_rejects_bad_cap():
    with pytest.raises(ValueError):
        optimize_hedge_capped(0.3, {}, _wide_universe(), 1_000_000.0, n_cap=0)


def test_frontier_trades_complexity_for_quality():
    # A large residual beta the size ceiling (15%) cannot neutralize with too few names:
    # 3 names -> max 0.45 of beta, can't reach 0.94; 10 names easily can.
    runs = complexity_frontier(
        residual_beta=0.94, residual_factors={"SMB": 0.10, "HML": -0.05, "MOM": 0.03},
        universe=_wide_universe(), long_aum=2_500_000.0, caps=(3, 5, 10),
    )
    assert [r.n_cap for r in runs] == [3, 5, 10]
    # More names -> hedge quality improves (tracking error is non-increasing).
    tes = [r.tracking_error for r in runs]
    assert tes[0] > tes[-1]
    assert tes == sorted(tes, reverse=True)
    # Too few names breaches the beta tolerance; enough names satisfies it.
    assert runs[0].beta_within_tol is False
    assert runs[-1].beta_within_tol is True


def test_frontier_caps_are_clamped_to_universe_size():
    runs = complexity_frontier(
        residual_beta=0.3, residual_factors={}, universe=_wide_universe(n=8),
        long_aum=1_000_000.0, caps=(10, 20, 50),
    )
    # All caps exceed the 8-name universe, so they collapse to a single run of 8.
    assert len(runs) == 1
    assert runs[0].n_cap == 8


def test_adaptive_caps_straddle_the_natural_support():
    # With caps=None the frontier derives caps from the uncapped support, so it shows a
    # real trade-off instead of identical rows even when the natural hedge is small.
    runs = complexity_frontier(
        residual_beta=0.94, residual_factors={"SMB": 0.10, "HML": -0.05, "MOM": 0.03},
        universe=_wide_universe(), long_aum=2_500_000.0, caps=None,
    )
    caps = [r.n_cap for r in runs]
    assert caps == sorted(set(caps))           # ascending, distinct
    assert caps[0] < caps[-1]                   # a genuine spread, not one flat point
    tes = [r.tracking_error for r in runs]
    assert tes == sorted(tes, reverse=True)     # quality improves with more names
    assert runs[-1].beta_within_tol is True     # the loosest cap neutralizes


def test_sector_concentration_breakdown_and_flag():
    positions = [
        ProposedShort("A", notional=600_000, beta=1.0, weight=0.06, sector="Technology"),
        ProposedShort("B", notional=300_000, beta=1.0, weight=0.03, sector="Energy"),
        ProposedShort("C", notional=100_000, beta=1.0, weight=0.01, sector="Healthcare"),
    ]
    sectors = sector_concentration(positions, sector_limit=0.50)
    # Sorted by share descending; fractions sum to 1.
    assert [s.sector for s in sectors] == ["Technology", "Energy", "Healthcare"]
    assert sectors[0].fraction == pytest.approx(0.60)
    assert sum(s.fraction for s in sectors) == pytest.approx(1.0)
    # Technology (60%) breaches a 50% limit; the others don't.
    assert sectors[0].breach is True
    assert all(not s.breach for s in sectors[1:])


def test_sector_limit_is_enforced_as_a_hard_constraint():
    # Technology has two cheap names that would otherwise dominate the hedge; the cap must
    # force the short book to spread across sectors instead of breaching.
    universe = [
        Candidate("T1", beta=1.2, sector="Technology"),
        Candidate("T2", beta=1.2, sector="Technology"),
        Candidate("E1", beta=1.0, sector="Energy"),
        Candidate("F1", beta=1.0, sector="Financials"),
        Candidate("H1", beta=1.0, sector="Healthcare"),
    ]
    common = dict(residual_beta=0.20, residual_factors={}, universe=universe,
                  long_aum=1_000_000.0, max_position_weight=0.5)
    tight = optimize_hedge(**common, sector_limit=0.30)
    assert abs(tight.net_beta_after) <= 0.05 + 1e-6  # still beta-neutral
    # No sector exceeds the cap, and nothing is flagged as breaching.
    assert all(s.fraction <= 0.30 + 1e-6 for s in tight.sectors)
    assert tight.sector_breaches == []


def test_sector_cap_fails_closed_when_unsatisfiable():
    # Only three sectors but a 30% cap (3 * 30% < 100%): no compliant hedge can neutralize
    # beta, so the optimizer fails closed rather than emitting a breaching proposal.
    universe = [
        Candidate("T1", beta=1.0, sector="Technology"),
        Candidate("E1", beta=1.0, sector="Energy"),
        Candidate("F1", beta=1.0, sector="Financials"),
    ]
    with pytest.raises(InfeasibleHedge):
        optimize_hedge(
            residual_beta=0.20, residual_factors={}, universe=universe,
            long_aum=1_000_000.0, max_position_weight=0.9, sector_limit=0.30,
        )

"""Hedge optimizer tests, anchored on the golden portfolio and the |net beta| <= 0.05
hard constraint."""
from __future__ import annotations

import pytest

from neptune.data.fixtures import GOLDEN_PORTFOLIO, golden_candidates, golden_positions
from neptune.domain.models import Portfolio
from neptune.quant.optimizer import (
    Candidate,
    InfeasibleHedge,
    compute_residual,
    optimize_hedge,
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
        assert short.notional <= ceiling + 1e-6
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

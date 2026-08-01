"""Hedge optimizer tests, anchored on the golden portfolio and the |net beta| <= 0.05
hard constraint."""
from __future__ import annotations

import numpy as np
import pytest

from neptune.data.fixtures import GOLDEN_PORTFOLIO, golden_candidates, golden_positions
from neptune.domain.models import Portfolio
from neptune.quant.factors import FACTORS
from neptune.quant.optimizer import (
    HEDGE_FACTORS,
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


def _net_book_variance(proposal, cands, residual_beta, residual_factors, F, hedge_factors):
    """Net-book daily-return variance for a proposal's basket: net_fullᵀ F net_full + Σ dᵢxᵢ²."""
    w = {p.ticker: p.weight for p in proposal.positions}
    x = np.array([w.get(c.ticker, 0.0) for c in cands])
    betas = np.array([c.beta for c in cands])
    loads = np.array([[c.loadings.get(f, 0.0) for f in hedge_factors] for c in cands])
    idio = np.array([(c.idio_var if c.idio_var is not None else c.variance) for c in cands])
    net_beta = residual_beta - betas @ x
    net_factors = np.array([residual_factors.get(f, 0.0) for f in hedge_factors]) - loads.T @ x
    net_full = np.concatenate([[net_beta], net_factors])
    return float(net_full @ F @ net_full + idio @ (x ** 2))


def _diag_psd_cov() -> np.ndarray:
    """A PSD factor covariance over [MKT, *HEDGE_FACTORS]: large market variance, smaller style
    variances, with a small MKT–SMB correlation to exercise the off-diagonal."""
    k = 1 + len(HEDGE_FACTORS)
    F = np.eye(k) * 0.3
    F[0, 0] = 1.0
    F[0, 1] = F[1, 0] = 0.1
    return F


def test_covariance_objective_minimizes_net_book_variance_and_keeps_neutrality():
    # Two beta-equal, SMB-equal hedges differing only in idiosyncratic risk, plus a pure-beta and
    # a low-beta name. The covariance objective should reach the global min-variance basket.
    smb = HEDGE_FACTORS[0]
    cands = [
        Candidate("A", beta=1.0, loadings={smb: 0.6}, idio_var=0.001),
        Candidate("B", beta=1.0, loadings={smb: 0.6}, idio_var=0.02),   # noisier twin of A
        Candidate("C", beta=1.0, loadings={smb: 0.0}, idio_var=0.001),  # pure beta
        Candidate("D", beta=0.2, loadings={smb: 0.6}, idio_var=0.001),
    ]
    residual_beta = 0.5
    residual_factors = {f: 0.0 for f in FACTORS}
    residual_factors[smb] = 0.3
    F = _diag_psd_cov()
    common = dict(residual_beta=residual_beta, residual_factors=residual_factors,
                  universe=cands, long_aum=1_000_000.0, beta_tol=BETA_TOL, factor_limit=0.20)

    diag = optimize_hedge(**common)                 # diagonal diversification objective
    cov = optimize_hedge(**common, factor_cov=F)    # net-book minimum-variance objective

    # The HARD invariant holds under BOTH objectives.
    assert abs(cov.net_beta_after) <= BETA_TOL + 1e-6
    assert all(abs(v) <= 0.20 + 1e-6 for v in cov.factor_after.values())
    # The covariance run achieves the global min net-book variance over the same feasible set,
    # so it is no worse than the diagonal run's (feasible) basket.
    var_cov = _net_book_variance(cov, cands, residual_beta, residual_factors, F, HEDGE_FACTORS)
    var_diag = _net_book_variance(diag, cands, residual_beta, residual_factors, F, HEDGE_FACTORS)
    assert var_cov <= var_diag + 1e-9
    # And it avoids piling into the high-idio twin B.
    w = {p.ticker: p.weight for p in cov.positions}
    assert w.get("B", 0.0) <= w.get("A", 0.0) + 1e-6


def test_promoted_factor_is_neutralized_when_in_hedge_factors():
    # A promoted monitor factor "BAB" added to the neutralized set must be held within the limit.
    hedge_factors = (*HEDGE_FACTORS, "BAB")
    cands = [
        Candidate("H1", beta=1.0, loadings={"BAB": 0.8}),
        Candidate("H2", beta=1.0, loadings={"BAB": -0.2}),
        Candidate("H3", beta=0.9, loadings={"BAB": 0.0}),
    ]
    residual_factors = {f: 0.0 for f in (*FACTORS, "BAB")}
    residual_factors["BAB"] = 0.3  # the book tilts on the promoted factor
    proposal = optimize_hedge(
        residual_beta=0.3, residual_factors=residual_factors, universe=cands,
        long_aum=1_000_000.0, beta_tol=BETA_TOL, factor_limit=0.20,
        max_position_weight=0.5, hedge_factors=hedge_factors,
    )
    assert abs(proposal.net_beta_after) <= BETA_TOL + 1e-6
    assert abs(proposal.factor_after["BAB"]) <= 0.20 + 1e-6  # promoted factor neutralized


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


def test_hedge_is_diversified_not_a_few_extreme_names():
    """A hedge must be a DIVERSIFIED basket — many moderate names, spread weight — not a few
    high-beta lottery names (the diversification/variance penalty). High-beta names carry high
    variance, so the optimizer prefers moderate-beta names and caps any single weight."""
    rng = np.random.default_rng(3)
    universe = []
    for i in range(200):
        beta = float(np.clip(rng.normal(1.0, 0.6), 0.2, 4.0))
        var = (0.01 + 0.02 * max(beta - 1, 0)) ** 2  # high beta → high variance
        universe.append(Candidate(f"U{i}", beta=beta, variance=var))

    proposal = optimize_hedge_capped(
        residual_beta=0.30, residual_factors={}, universe=universe, long_aum=2_500_000.0,
        n_cap=35, beta_tol=BETA_TOL, max_position_weight=0.15,
    )
    assert proposal.beta_within_tol                       # neutral
    assert proposal.n_selected >= 20                      # diversified, not a handful
    betas = [s.beta for s in proposal.positions]
    assert max(betas) < 2.5                               # avoids the beta-4 lottery names
    assert max(s.weight for s in proposal.positions) < 0.10  # weight is spread, not concentrated


def test_beta_add_budget_fences_negative_beta_shorts():
    """Option A fence: shorting a negative-beta name to match a factor tilt ADDS market beta
    (−βᵢ·xᵢ > 0 when βᵢ < 0). Unfenced, the optimizer leans on such a name to kill the SMB tilt;
    the budget caps the aggregate beta the shorts may add — Σ max(0, −βᵢ·xᵢ) ≤ budget — while net
    beta stays neutral. The factor limit is left loose so the fence (not infeasibility) is tested."""
    universe = [
        Candidate("POS", beta=1.0, loadings={"SMB": 0.0}),   # pure beta hedge (removes beta)
        Candidate("NEGF", beta=-1.0, loadings={"SMB": 1.0}),  # neg-beta, strong SMB tilt
    ]
    common = dict(
        residual_beta=0.30, residual_factors={"SMB": 0.30, "HML": 0.0, "MOM": 0.0},
        universe=universe, long_aum=1_000_000.0, beta_tol=BETA_TOL,
        factor_limit=0.50, max_position_weight=1.0,  # loose factor limit → fence is the binding test
    )

    def beta_added(prop):  # net market beta the shorts inject (only negative-beta names contribute)
        return sum(max(0.0, -p.beta * p.weight) for p in prop.positions)

    free = optimize_hedge(**common)
    fenced = optimize_hedge(**common, beta_add_budget=0.02)

    assert beta_added(free) > 0.05                  # unfenced: shorts add real market beta
    assert beta_added(fenced) <= 0.02 + 1e-6        # fenced: capped to the budget
    assert beta_added(fenced) < beta_added(free)    # the fence actually binds
    assert abs(fenced.net_beta_after) <= BETA_TOL + 1e-6  # still beta-neutral


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

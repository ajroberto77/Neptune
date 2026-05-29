"""Two-pass hedge optimizer.

The systematic short book exists ONLY to neutralize long-book market beta (and reduce
residual factor exposure) — it is never an alpha source (CLAUDE.md, layer-5). The
optimizer therefore minimizes *tracking error to the residual exposure*, not return.

  Pass 1: compute the residual beta / factor exposure left by the long book plus any
          discretionary shorts (discretionary shorts are inputs, never modified — I-04).
  Pass 2: choose systematic short weights x_i >= 0 over the shortable universe to
          minimize residual exposure, subject to the HARD constraints:
            * |net beta| <= beta_tol                (default 0.05)
            * |net factor[f]| <= factor_limit       (Size/Value/Momentum)
            * 0 <= x_i <= max_position_weight        (15% of long AUM)

Output is a PROPOSAL with status PENDING_APPROVAL. Neptune never executes (I-01).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cvxpy as cp
import numpy as np

from neptune.quant.factors import FACTORS

HEDGE_FACTORS = ("SMB", "HML", "MOM")  # market beta is constrained separately
ZERO_WEIGHT_TOL = 1e-6


class InfeasibleHedge(RuntimeError):
    """Raised when no short basket can satisfy the hard beta/factor constraints.

    This is a legitimate domain state (the universe cannot hedge the book to neutral),
    not a bug. We fail closed rather than emit a proposal that breaches |net beta|<=tol.
    """


@dataclass
class Candidate:
    """A shortable-universe name the optimizer may select."""

    ticker: str
    beta: float
    loadings: dict[str, float] = field(default_factory=dict)
    sector: str | None = None


@dataclass
class ProposedShort:
    ticker: str
    notional: float       # positive dollar magnitude to short
    beta: float
    weight: float         # fraction of long AUM


@dataclass
class HedgeProposal:
    """A recommendation, not a command. ``status`` is always PENDING_APPROVAL."""

    positions: list[ProposedShort]
    net_beta_before: float
    net_beta_after: float
    factor_before: dict[str, float]
    factor_after: dict[str, float]
    long_aum: float
    status: str = "PENDING_APPROVAL"
    solver_status: str = ""


def compute_residual(
    positions: list[tuple[float, float, dict[str, float]]],
    long_aum: float,
) -> tuple[float, dict[str, float]]:
    """Pass 1. ``positions`` is a list of (signed_notional, beta, loadings) over the
    long book + discretionary shorts. Returns (residual_beta, residual_factors), both
    normalized by long AUM (i.e. in beta units)."""
    if long_aum <= 0:
        raise ValueError("long_aum must be positive")
    dollar_beta = sum(sn * beta for sn, beta, _ in positions)
    residual_factors = {f: 0.0 for f in FACTORS}
    for sn, _beta, loadings in positions:
        for f, load in loadings.items():
            if f in residual_factors:  # ignore keys outside the known factor set
                residual_factors[f] += sn * load
    residual_factors = {f: v / long_aum for f, v in residual_factors.items()}
    return dollar_beta / long_aum, residual_factors


def optimize_hedge(
    residual_beta: float,
    residual_factors: dict[str, float],
    universe: list[Candidate],
    long_aum: float,
    beta_tol: float = 0.05,
    factor_limit: float = 0.20,
    max_position_weight: float = 0.15,
    excluded_tickers: set[str] | None = None,
) -> HedgeProposal:
    """Pass 2. Solve the QP and return a hedge proposal (pending approval)."""
    excluded = excluded_tickers or set()
    cands = [c for c in universe if c.ticker not in excluded]
    if not cands:
        raise ValueError("empty shortable universe after exclusions")

    n = len(cands)
    betas = np.array([c.beta for c in cands])
    # Factor loading matrix: rows = candidates, cols = hedge factors.
    loads = np.array([[c.loadings.get(f, 0.0) for f in HEDGE_FACTORS] for c in cands])

    x = cp.Variable(n, nonneg=True)  # short weight as fraction of long AUM (>= 0)

    # Shorting subtracts exposure: net = residual - sum(x_i * exposure_i).
    net_beta = residual_beta - betas @ x
    resid_fac = np.array([residual_factors.get(f, 0.0) for f in HEDGE_FACTORS])
    net_factors = resid_fac - loads.T @ x

    objective = cp.Minimize(cp.square(net_beta) + cp.sum_squares(net_factors))
    constraints = [
        cp.abs(net_beta) <= beta_tol,
        x <= max_position_weight,
    ]
    for j in range(len(HEDGE_FACTORS)):
        constraints.append(cp.abs(net_factors[j]) <= factor_limit)

    problem = cp.Problem(objective, constraints)
    problem.solve(solver=cp.CLARABEL)

    if problem.status not in ("optimal", "optimal_inaccurate"):
        raise InfeasibleHedge(
            f"no feasible hedge under |net beta| <= {beta_tol} with this universe "
            f"(solver status: {problem.status})"
        )

    weights = np.asarray(x.value).flatten()
    proposed = [
        ProposedShort(
            ticker=c.ticker,
            notional=float(wt * long_aum),
            beta=c.beta,
            weight=float(wt),
        )
        for c, wt in zip(cands, weights)
        if wt > ZERO_WEIGHT_TOL
    ]

    net_beta_after = float(residual_beta - betas @ weights)
    factor_after = {
        f: float(residual_factors.get(f, 0.0) - loads[:, j] @ weights)
        for j, f in enumerate(HEDGE_FACTORS)
    }

    return HedgeProposal(
        positions=proposed,
        net_beta_before=float(residual_beta),
        net_beta_after=net_beta_after,
        factor_before={f: residual_factors.get(f, 0.0) for f in HEDGE_FACTORS},
        factor_after=factor_after,
        long_aum=long_aum,
        solver_status=problem.status,
    )

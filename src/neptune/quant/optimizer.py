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
            * sector short share <= sector_limit     (per-GICS sector, default 30%)

Output is a PROPOSAL with status PENDING_APPROVAL. Neptune never executes (I-01).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cvxpy as cp
import numpy as np

from neptune.quant.factors import FACTORS

HEDGE_FACTORS = ("SMB", "HML", "MOM")  # market beta is constrained separately
ZERO_WEIGHT_TOL = 1e-6
# L1 penalty on gross short (sum of weights). Without it the tracking-error objective is
# degenerate once beta/factors are neutralized — any allocation in the null space is equally
# optimal, so the interior-point solver returns a DENSE basket (a tiny short in every name).
# A small positive penalty makes the minimal-gross hedge unique and SPARSE: the optimizer
# keeps only the few names that efficiently reproduce the book's beta/factor exposures, and
# zeroes the rest. Kept small so it only breaks ties — it never overrides the hard neutrality
# constraints (which still pull net beta/factors to ~0 when far from neutral).
GROSS_PENALTY = 1e-3


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
    sector: str | None = None


@dataclass
class SectorConcentration:
    """One GICS sector's share of total short notional, and whether it breaches the
    (PM-adjustable) concentration limit. This is a SOFT warning, never an execution
    block — the optimizer flags it; a human decides (CLAUDE.md §2)."""

    sector: str
    notional: float
    fraction: float       # notional / total short notional
    limit: float
    breach: bool


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
    n_cap: int | None = None          # position-count cap for this run (None = uncapped)
    tracking_error: float = 0.0       # sqrt(net_beta^2 + sum factor^2) after hedging
    beta_within_tol: bool = False     # whether |net beta after| <= beta_tol (fail-safe default)
    sector_limit: float = 0.30        # the concentration limit these flags were computed against
    sectors: list[SectorConcentration] = field(default_factory=list)

    @property
    def n_selected(self) -> int:
        return len(self.positions)

    @property
    def sector_breaches(self) -> list[str]:
        return [s.sector for s in self.sectors if s.breach]


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
    sector_limit: float = 0.30,
    excluded_tickers: set[str] | None = None,
) -> HedgeProposal:
    """Pass 2 (recommended run). Solve the QP and return a hedge proposal.

    Market-beta neutrality |net beta| <= beta_tol, the factor limits, AND the per-sector
    concentration cap (``sector_limit``) are all HARD constraints; the run fails closed
    (InfeasibleHedge) if they cannot be jointly met with this universe.
    """
    excluded = excluded_tickers or set()
    cands = [c for c in universe if c.ticker not in excluded]
    if not cands:
        raise ValueError(
            "no shortable candidates: the hedge universe is your backfilled names "
            "minus current longs. Backfill more names (the instruments you'd short to "
            "hedge) so the optimizer has something to work with."
        )

    weights, status = _solve_qp(
        residual_beta, residual_factors, cands, beta_tol, factor_limit,
        max_position_weight, hard=True, sector_limit=sector_limit,
    )
    if status not in ("optimal", "optimal_inaccurate"):
        raise InfeasibleHedge(
            f"no feasible hedge under |net beta| <= {beta_tol}, factor limit {factor_limit}, "
            f"and sector cap {sector_limit:.0%} with this universe (solver status: {status})"
        )
    return _build_proposal(
        cands, weights, residual_beta, residual_factors, long_aum, status,
        beta_tol=beta_tol, sector_limit=sector_limit,
    )


def optimize_hedge_capped(
    residual_beta: float,
    residual_factors: dict[str, float],
    universe: list[Candidate],
    long_aum: float,
    n_cap: int,
    beta_tol: float = 0.05,
    factor_limit: float = 0.20,
    max_position_weight: float = 0.15,
    sector_limit: float = 0.30,
    excluded_tickers: set[str] | None = None,
) -> HedgeProposal:
    """Capped run: select at most ``n_cap`` names, then size them.

    True MIQP (binary selection) needs a commercial solver; per the roadmap we use the
    documented greedy + QP approximation: solve the uncapped problem, keep the ``n_cap``
    largest holdings by hedging contribution, then re-optimize sizing over just those
    names. Capped runs use SOFT objectives (no hard beta/factor constraint) so a frontier
    point is always returned — its ``beta_within_tol`` flag reports whether |net beta| <=
    tol was actually achieved. These runs are exploratory, never an approvable book that
    silently breaches the hard tolerance.
    """
    if n_cap < 1:
        raise ValueError("n_cap must be >= 1")
    excluded = excluded_tickers or set()
    cands = [c for c in universe if c.ticker not in excluded]
    if not cands:
        raise ValueError(
            "no shortable candidates: the hedge universe is your backfilled names "
            "minus current longs. Backfill more names (the instruments you'd short to "
            "hedge) so the optimizer has something to work with."
        )

    ranked = _rank_candidates(
        residual_beta, residual_factors, cands, beta_tol, factor_limit, max_position_weight
    )
    return _size_capped(
        ranked, n_cap, residual_beta, residual_factors, long_aum,
        beta_tol, factor_limit, max_position_weight, sector_limit,
    )


def complexity_frontier(
    residual_beta: float,
    residual_factors: dict[str, float],
    universe: list[Candidate],
    long_aum: float,
    caps: tuple[int, ...] | None = None,
    beta_tol: float = 0.05,
    factor_limit: float = 0.20,
    max_position_weight: float = 0.15,
    sector_limit: float = 0.30,
    excluded_tickers: set[str] | None = None,
) -> list[HedgeProposal]:
    """Run a series of capped optimizations to expose the complexity-quality trade-off.

    The uncapped soft solve is run once to rank names; when ``caps`` is None we then find
    the *neutralization threshold* k (the smallest number of top-ranked names that gets
    |net beta| <= tol, via binary search) and derive caps straddling it
    (``_adaptive_caps`` ~ k/3, 2k/3, k) so the frontier always shows a real trade-off
    rather than identical rows — the roadmap's fixed N<=10/20/50 assume a
    Russell-3000-scale hedge and go degenerate on a small book (where the soft solve
    sprinkles tiny weights across the whole universe). Explicit ``caps`` are honored
    (clamped to the universe, deduplicated). Returns proposals sorted by ``n_cap``
    ascending: fewer names -> higher tracking error / possible beta breach.
    """
    excluded = excluded_tickers or set()
    usable = [c for c in universe if c.ticker not in excluded]
    if not usable:
        raise ValueError(
            "no shortable candidates: the hedge universe is your backfilled names "
            "minus current longs. Backfill more names (the instruments you'd short to "
            "hedge) so the optimizer has something to work with."
        )

    ranked = _rank_candidates(
        residual_beta, residual_factors, usable, beta_tol, factor_limit, max_position_weight
    )
    if caps is not None:
        requested: tuple[int, ...] = caps
    else:
        k = _min_neutralizing_cap(
            ranked, residual_beta, residual_factors, beta_tol, factor_limit,
            max_position_weight,
        )
        requested = _adaptive_caps(k)
    distinct = sorted({min(c, len(usable)) for c in requested if c >= 1})
    return [
        _size_capped(
            ranked, n_cap, residual_beta, residual_factors, long_aum,
            beta_tol, factor_limit, max_position_weight, sector_limit,
        )
        for n_cap in distinct
    ]


def _adaptive_caps(k: int) -> tuple[int, ...]:
    """Three caps that straddle the neutralization threshold ``k`` so the frontier is
    informative on any book size.

    ``k`` is the smallest cap that achieves beta neutrality. Returning roughly
    (k/3, 2k/3, k) makes the tightest run under-hedge (a beta breach), the middle trade
    off, and the loosest just reach neutrality — the trade-off the panel is meant to
    show. Tiny ``k`` collapses to fewer distinct points."""
    k = max(1, k)
    return (max(1, round(k / 3)), max(1, round(2 * k / 3)), k)


def _min_neutralizing_cap(
    ranked: list[Candidate],
    residual_beta: float,
    residual_factors: dict[str, float],
    beta_tol: float,
    factor_limit: float,
    max_position_weight: float,
) -> int:
    """Smallest number of top-ranked names whose soft-sized hedge gets |net beta| <= tol.

    Found by binary search over the ranked prefix (~log2(N) extra QP solves). If even the
    full set cannot neutralize, returns the universe size so the loosest frontier point is
    the best achievable (honestly flagged as a breach)."""
    n = len(ranked)

    def neutralizes(k: int) -> bool:
        subset = ranked[:k]
        weights, _ = _solve_qp(
            residual_beta, residual_factors, subset, beta_tol, factor_limit,
            max_position_weight, hard=False,
        )
        betas = np.array([c.beta for c in subset])
        return abs(residual_beta - betas @ weights) <= beta_tol

    if not neutralizes(n):
        return n
    lo, hi = 1, n
    while lo < hi:
        mid = (lo + hi) // 2
        if neutralizes(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


# --- shared solve / proposal helpers ---------------------------------------------

def _solve_qp(
    residual_beta: float,
    residual_factors: dict[str, float],
    cands: list[Candidate],
    beta_tol: float,
    factor_limit: float,
    max_position_weight: float,
    hard: bool,
    sector_limit: float | None = None,
) -> tuple[np.ndarray, str]:
    """Minimize tracking error to the residual. With ``hard``, the beta/factor limits
    (and, when given, the per-sector concentration cap) are constraints; otherwise only the
    per-name size ceiling is enforced (the limits live in the objective), guaranteeing a
    feasible solution."""
    betas = np.array([c.beta for c in cands])
    loads = np.array([[c.loadings.get(f, 0.0) for f in HEDGE_FACTORS] for c in cands])

    x = cp.Variable(len(cands), nonneg=True)  # short weight as fraction of long AUM
    # Shorting subtracts exposure: net = residual - sum(x_i * exposure_i).
    net_beta = residual_beta - betas @ x
    resid_fac = np.array([residual_factors.get(f, 0.0) for f in HEDGE_FACTORS])
    net_factors = resid_fac - loads.T @ x

    # Tracking error to the residual + an L1 gross-short penalty for a sparse, minimal basket.
    objective = cp.Minimize(
        cp.square(net_beta) + cp.sum_squares(net_factors) + GROSS_PENALTY * cp.sum(x)
    )
    constraints = [x <= max_position_weight]
    if hard:
        constraints.append(cp.abs(net_beta) <= beta_tol)
        for j in range(len(HEDGE_FACTORS)):
            constraints.append(cp.abs(net_factors[j]) <= factor_limit)
        if sector_limit is not None:
            # Sector concentration is a HARD constraint: each sector's share of total short
            # notional <= sector_limit. Linear (hence convex): share_s = sum(x in s) / sum(x),
            # so sum(x in s) <= sector_limit * sum(x). The optimizer never proposes a hedge
            # that breaches the limit (it spreads the short book across sectors instead).
            by_sector: dict[str, list[int]] = {}
            for i, c in enumerate(cands):
                if c.sector is None:
                    continue  # can't cap a name whose sector we don't know — leave it free
                by_sector.setdefault(c.sector, []).append(i)
            total_short = cp.sum(x)
            for idxs in by_sector.values():
                constraints.append(cp.sum(x[idxs]) <= sector_limit * total_short)

    problem = cp.Problem(objective, constraints)
    problem.solve(solver=cp.CLARABEL)
    weights = np.asarray(x.value).flatten() if x.value is not None else np.zeros(len(cands))
    return weights, problem.status


def _rank_candidates(
    residual_beta: float,
    residual_factors: dict[str, float],
    cands: list[Candidate],
    beta_tol: float,
    factor_limit: float,
    max_position_weight: float,
) -> list[Candidate]:
    """Uncapped soft solve, then rank names by total hedging contribution, best-first.

    The key is ``|w| * ||(beta, loadings)||`` — the Euclidean norm of the exposure a name
    removes per unit weight (market beta plus style loadings) — so a name valuable purely
    for factor-neutralizing is not pruned in favor of a pure-beta name."""
    full_weights, _ = _solve_qp(
        residual_beta, residual_factors, cands, beta_tol, factor_limit,
        max_position_weight, hard=False,
    )
    contribution = np.array([
        abs(w) * np.sqrt(c.beta**2 + sum(c.loadings.get(f, 0.0) ** 2 for f in HEDGE_FACTORS))
        for w, c in zip(full_weights, cands)
    ])
    order = np.argsort(-contribution)
    return [cands[i] for i in order]


def _size_capped(
    ranked: list[Candidate],
    n_cap: int,
    residual_beta: float,
    residual_factors: dict[str, float],
    long_aum: float,
    beta_tol: float,
    factor_limit: float,
    max_position_weight: float,
    sector_limit: float = 0.30,
) -> HedgeProposal:
    """Re-optimize sizing over the top ``n_cap`` ranked names (soft QP)."""
    subset = ranked[: min(n_cap, len(ranked))]
    weights, status = _solve_qp(
        residual_beta, residual_factors, subset, beta_tol, factor_limit,
        max_position_weight, hard=False,
    )
    # The soft QP is always feasible; a non-optimal status means a genuine solver
    # failure, which we surface rather than returning a silent zero hedge.
    if status not in ("optimal", "optimal_inaccurate"):
        raise InfeasibleHedge(f"capped hedge solve failed (solver status: {status})")
    return _build_proposal(
        subset, weights, residual_beta, residual_factors, long_aum, status,
        beta_tol=beta_tol, sector_limit=sector_limit, n_cap=n_cap,
    )


def sector_concentration(
    positions: list[ProposedShort], sector_limit: float
) -> list[SectorConcentration]:
    """Each sector's share of total short notional, flagged against ``sector_limit``.

    A soft warning (CLAUDE.md §2): the optimizer surfaces over-concentration; it never
    blocks. Sorted by share descending. Names with no sector are bucketed as "Unknown"."""
    total = sum(p.notional for p in positions)
    if total <= 0:
        return []
    by_sector: dict[str, float] = {}
    for p in positions:
        by_sector[p.sector or "Unknown"] = by_sector.get(p.sector or "Unknown", 0.0) + p.notional
    # A tolerance so a sector sitting exactly on the hard cap (the efficient sparse hedge
    # pushes right up to it) isn't flagged as a breach by floating-point noise.
    out = [
        SectorConcentration(
            sector=sec, notional=notional, fraction=notional / total,
            limit=sector_limit, breach=(notional / total) > sector_limit + 1e-6,
        )
        for sec, notional in by_sector.items()
    ]
    return sorted(out, key=lambda s: s.fraction, reverse=True)


def _build_proposal(
    cands: list[Candidate],
    weights: np.ndarray,
    residual_beta: float,
    residual_factors: dict[str, float],
    long_aum: float,
    solver_status: str,
    beta_tol: float,
    sector_limit: float,
    n_cap: int | None = None,
) -> HedgeProposal:
    betas = np.array([c.beta for c in cands])
    loads = np.array([[c.loadings.get(f, 0.0) for f in HEDGE_FACTORS] for c in cands])

    proposed = [
        ProposedShort(ticker=c.ticker, notional=float(wt * long_aum), beta=c.beta,
                      weight=float(wt), sector=c.sector)
        for c, wt in zip(cands, weights)
        if wt > ZERO_WEIGHT_TOL
    ]
    net_beta_after = float(residual_beta - betas @ weights)
    factor_after = {
        f: float(residual_factors.get(f, 0.0) - loads[:, j] @ weights)
        for j, f in enumerate(HEDGE_FACTORS)
    }
    tracking_error = float(
        np.sqrt(net_beta_after**2 + sum(v**2 for v in factor_after.values()))
    )
    return HedgeProposal(
        positions=proposed,
        net_beta_before=float(residual_beta),
        net_beta_after=net_beta_after,
        factor_before={f: residual_factors.get(f, 0.0) for f in HEDGE_FACTORS},
        factor_after=factor_after,
        long_aum=long_aum,
        solver_status=solver_status,
        n_cap=n_cap,
        tracking_error=tracking_error,
        beta_within_tol=abs(net_beta_after) <= beta_tol,
        sector_limit=sector_limit,
        sectors=sector_concentration(proposed, sector_limit),
    )

"""Bridge between domain positions and the Quant Engine's numeric inputs.

For the vertical slice a position's beta is its PM ``forward_beta`` when set, else a
neutral 1.0 placeholder (live betas will come from the beta pipeline once price
ingestion exists). Factor loadings default to zero in the slice.
"""
from __future__ import annotations

from neptune.domain.models import Portfolio, Position, ShortType

DEFAULT_BETA = 1.0


def position_beta(position: Position) -> float:
    """Resolve the beta to use for a position (forward override wins)."""
    return position.forward_beta if position.forward_beta is not None else DEFAULT_BETA


def position_loadings(position: Position) -> dict[str, float]:
    """Factor loadings for a position. Zero in the slice (no live factor data)."""
    return {}


def net_beta(portfolio: Portfolio) -> float:
    """Dollar-beta-weighted net beta across the whole book, normalized by long AUM."""
    if portfolio.long_aum <= 0:
        raise ValueError("portfolio has no long AUM")
    dollar_beta = sum(p.signed_notional * position_beta(p) for p in portfolio.positions)
    return dollar_beta / portfolio.long_aum


def residual_inputs(portfolio: Portfolio) -> list[tuple[float, float, dict[str, float]]]:
    """(signed_notional, beta, loadings) tuples for Pass 1 of the optimizer.

    Includes the long book and discretionary shorts only — the systematic short book
    is what the optimizer is about to (re)propose, so it is excluded from the residual.
    """
    return [
        (p.signed_notional, position_beta(p), position_loadings(p))
        for p in portfolio.positions
        if p.short_type is not ShortType.SYSTEMATIC
    ]

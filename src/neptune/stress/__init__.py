"""Stress Engine — scenario shocks and parametric VaR/ES.

Pure functions over exposures (no DB, no network, no I/O — like the rest of the Quant
Engine, CLAUDE.md layer 1). The Risk Interface builds the exposure list and the factor
covariance from market data and feeds them in; this module does the math.

A position's shocked return under a scenario is driven by the same five-factor model
used everywhere else:

    r_i = beta_i * market_shock + sum_f loading_i[f] * factor_shock[f]

and its P&L impact is ``signed_notional_i * r_i`` (a long loses when the market falls,
a short gains). Results are reported split by book (Long / Systematic / Discretionary),
never conflating the two short books (invariant I-03).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# The style factors carried alongside the market in a scenario / covariance.
STYLE_FACTORS = ("SMB", "HML", "MOM")
RISK_FACTORS = ("MKT", *STYLE_FACTORS)


@dataclass(frozen=True)
class StressExposure:
    """One position's inputs to the stress engine."""

    ticker: str
    book: str                 # BookType value — kept as a plain string to stay pure
    signed_notional: float    # +long / -short, in base currency
    beta: float
    loadings: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    """A named shock. ``market_shock`` and each ``factor_shocks`` value are returns
    (e.g. -0.10 = a 10% drop)."""

    name: str
    market_shock: float = 0.0
    factor_shocks: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    total_pnl: float
    by_book: dict[str, float]
    by_position: dict[str, float]
    net_beta_dollar_before: float   # sum(signed_notional * beta)
    market_shock: float


@dataclass(frozen=True)
class VaRResult:
    confidence: float
    horizon_days: int
    volatility: float        # 1-sigma P&L (currency) over the horizon
    var: float               # positive number = potential loss at the confidence level
    expected_shortfall: float  # average loss beyond VaR (positive number)


def position_shock_return(exposure: StressExposure, scenario: Scenario) -> float:
    """The shocked return of one name under the scenario (factor model)."""
    r = exposure.beta * scenario.market_shock
    for factor, shock in scenario.factor_shocks.items():
        r += exposure.loadings.get(factor, 0.0) * shock
    return r


def apply_scenario(exposures: list[StressExposure], scenario: Scenario) -> ScenarioResult:
    """Deterministic P&L impact of a scenario, split by book."""
    by_book: dict[str, float] = {}
    by_position: dict[str, float] = {}
    total = 0.0
    net_beta_dollar = 0.0
    for e in exposures:
        pnl = e.signed_notional * position_shock_return(e, scenario)
        by_position[e.ticker] = by_position.get(e.ticker, 0.0) + pnl
        by_book[e.book] = by_book.get(e.book, 0.0) + pnl
        total += pnl
        net_beta_dollar += e.signed_notional * e.beta
    return ScenarioResult(
        name=scenario.name,
        total_pnl=total,
        by_book=by_book,
        by_position=by_position,
        net_beta_dollar_before=net_beta_dollar,
        market_shock=scenario.market_shock,
    )


def dollar_factor_exposures(
    exposures: list[StressExposure], factors: tuple[str, ...] = RISK_FACTORS
) -> np.ndarray:
    """Net dollar exposure vector e, where e[MKT] = sum(N_i * beta_i) and
    e[f] = sum(N_i * loading_i[f]). This is the portfolio's sensitivity to a unit move
    in each risk factor — the input to parametric VaR."""
    e = np.zeros(len(factors))
    for i, f in enumerate(factors):
        if f == "MKT":
            e[i] = sum(x.signed_notional * x.beta for x in exposures)
        else:
            e[i] = sum(x.signed_notional * x.loadings.get(f, 0.0) for x in exposures)
    return e


def _z_score(confidence: float) -> float:
    """One-sided normal quantile for a confidence level (e.g. 0.95 -> 1.645)."""
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be in (0.5, 1.0)")
    # Inverse standard normal CDF via the rational approximation (Acklam).
    return _norm_ppf(confidence)


def parametric_var(
    exposures: list[StressExposure],
    factor_cov: np.ndarray,
    confidence: float = 0.95,
    horizon_days: int = 1,
    factors: tuple[str, ...] = RISK_FACTORS,
) -> VaRResult:
    """Parametric (variance-covariance) VaR and Expected Shortfall.

    ``factor_cov`` is the covariance matrix of the RISK_FACTORS' periodic returns
    (same order as ``factors``). Portfolio P&L variance = eᵀ Σ e on the dollar exposure
    vector. VaR = z * sigma * sqrt(horizon); ES = phi(z)/(1-c) * sigma * sqrt(horizon).
    Both are returned as positive potential losses.
    """
    cov = np.asarray(factor_cov, dtype=float)
    n = len(factors)
    if cov.shape != (n, n):
        raise ValueError(f"factor_cov must be {n}x{n} for factors {factors}")
    e = dollar_factor_exposures(exposures, factors)
    variance = float(e @ cov @ e)
    variance = max(variance, 0.0)
    sigma = math.sqrt(variance) * math.sqrt(horizon_days)
    z = _z_score(confidence)
    alpha = 1.0 - confidence
    es_multiplier = _norm_pdf(z) / alpha
    return VaRResult(
        confidence=confidence,
        horizon_days=horizon_days,
        volatility=sigma,
        var=z * sigma,
        expected_shortfall=es_multiplier * sigma,
    )


# --- standard-normal helpers (kept local so the engine has no SciPy dependency) ---

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation, ~1e-9 accurate)."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# --- a small library of standard scenarios ---------------------------------------

STANDARD_SCENARIOS: tuple[Scenario, ...] = (
    Scenario("Market -10%", market_shock=-0.10),
    Scenario("Market +10%", market_shock=0.10),
    Scenario("Risk-off (mkt -15%, value +5%, mom -10%)", market_shock=-0.15,
             factor_shocks={"HML": 0.05, "MOM": -0.10}),
    Scenario("Small-cap selloff (mkt -8%, size -12%)", market_shock=-0.08,
             factor_shocks={"SMB": -0.12}),
)

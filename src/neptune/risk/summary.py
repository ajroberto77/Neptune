"""Human-facing risk summary: net beta and factor exposures with status badges."""
from __future__ import annotations

from dataclasses import dataclass

OK = "OK"
WATCH = "WATCH"
BREACH = "BREACH"


def classify(value: float, limit: float) -> str:
    """Badge for an exposure against a limit: OK below half the limit, WATCH up to the
    limit, BREACH at or beyond it."""
    mag = abs(value)
    if mag >= limit:
        return BREACH
    if mag >= 0.5 * limit:
        return WATCH
    return OK


@dataclass
class FactorStatus:
    factor: str
    exposure: float
    limit: float
    status: str


@dataclass
class RiskSummary:
    net_beta: float
    beta_tol: float
    beta_status: str
    factors: list[FactorStatus]
    long_aum: float

    @property
    def beta_neutral(self) -> bool:
        """True iff the net-beta hard constraint |beta| <= tol holds."""
        return abs(self.net_beta) <= self.beta_tol

    def headline(self) -> str:
        state = "beta-neutral" if self.beta_neutral else "OUTSIDE tolerance"
        return (
            f"Net beta {self.net_beta:+.4f} ({state}; limit +/-{self.beta_tol:.2f}). "
            f"{sum(f.status == BREACH for f in self.factors)} factor breach(es)."
        )


def summarize(
    net_beta: float,
    factor_exposures: dict[str, float],
    long_aum: float,
    beta_tol: float = 0.05,
    factor_limit: float = 0.20,
) -> RiskSummary:
    """Build a RiskSummary from engine outputs."""
    factors = [
        FactorStatus(factor=f, exposure=v, limit=factor_limit, status=classify(v, factor_limit))
        for f, v in factor_exposures.items()
    ]
    return RiskSummary(
        net_beta=net_beta,
        beta_tol=beta_tol,
        beta_status=classify(net_beta, beta_tol),
        factors=factors,
        long_aum=long_aum,
    )

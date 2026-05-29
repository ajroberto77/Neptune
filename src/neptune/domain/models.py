"""Core domain dataclasses and enums.

These are plain Python objects with no persistence or framework coupling, so the
Quant Engine can consume them directly. The Fundamental Layer (``thesis`` and
``target``) is carried here as **read-only input** — no Neptune module may generate
or mutate it (see CLAUDE.md, layer 3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Side(str, Enum):
    """Whether a position is long or short."""

    LONG = "LONG"
    SHORT = "SHORT"


class ShortType(str, Enum):
    """Why a short exists. Systematic shorts are optimizer-generated hedges;
    discretionary shorts are human ideas. These are NEVER conflated (invariant I-03),
    and the optimizer never mutates a discretionary short (I-04)."""

    SYSTEMATIC = "SYSTEMATIC"        # optimizer hedge — neutralizes long-book beta
    DISCRETIONARY = "DISCRETIONARY"  # PM/analyst idea — an input to the optimizer
    NA = "NA"                        # not applicable (long positions)


@dataclass
class Position:
    """A single book position.

    ``notional`` is always a positive dollar magnitude; direction is given by
    ``side``. ``forward_beta``, when set by a PM, supersedes the entire beta pipeline
    for this position (post-catalyst override).

    ``thesis`` and ``target`` belong to the Fundamental Layer: Neptune reads them but
    must never write or auto-generate them.
    """

    ticker: str
    side: Side
    notional: float
    short_type: ShortType = ShortType.NA
    forward_beta: float | None = None
    sector: str | None = None
    # --- Fundamental Layer (read-only to the system) ---
    thesis: str | None = None
    target: str | None = None

    def __post_init__(self) -> None:
        if self.notional < 0:
            raise ValueError("notional must be a positive magnitude; use `side` for direction")
        if self.side is Side.LONG and self.short_type is not ShortType.NA:
            raise ValueError("long positions must have short_type=NA")
        if self.side is Side.SHORT and self.short_type is ShortType.NA:
            raise ValueError("short positions must declare a short_type (SYSTEMATIC/DISCRETIONARY)")

    @property
    def signed_notional(self) -> float:
        """Notional with sign: positive for long, negative for short."""
        return self.notional if self.side is Side.LONG else -self.notional


@dataclass
class Portfolio:
    """A book of positions belonging to one PM mandate."""

    id: str
    name: str
    base_currency: str = "USD"
    is_paper: bool = False
    positions: list[Position] = field(default_factory=list)

    @property
    def longs(self) -> list[Position]:
        return [p for p in self.positions if p.side is Side.LONG]

    @property
    def systematic_shorts(self) -> list[Position]:
        return [p for p in self.positions if p.short_type is ShortType.SYSTEMATIC]

    @property
    def discretionary_shorts(self) -> list[Position]:
        return [p for p in self.positions if p.short_type is ShortType.DISCRETIONARY]

    @property
    def long_aum(self) -> float:
        """Total long notional — the denominator for beta/factor normalization."""
        return sum(p.notional for p in self.longs)

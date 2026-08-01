"""Core domain dataclasses and enums.

These are plain Python objects with no persistence or framework coupling, so the
Quant Engine can consume them directly. The Fundamental Layer (``thesis`` and
``target``) is carried here as **read-only input** — no Neptune module may generate
or mutate it (see CLAUDE.md, layer 3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

from neptune.pnl import CostBasisMethod


class Side(str, Enum):
    """Whether a position is long or short."""

    LONG = "LONG"
    SHORT = "SHORT"


class TradeAction(str, Enum):
    """A manual trade ticket is just a direction. The effect on the book is *derived* from
    the current holding (netting): a BUY covers an open short then opens/adds a long; a SELL
    reduces an open long then opens/adds a (discretionary) short. Crossing zero flips side."""

    BUY = "BUY"
    SELL = "SELL"


class TradeOrigin(str, Enum):
    """Where an executed trade came from. MANUAL = a desk ticket (Trade tab); HEDGE = booked
    by approving an optimizer hedge proposal (the systematic-short covers and new shorts).
    Recorded on every ledger row so the blotter can distinguish desk trades from hedge churn."""

    MANUAL = "MANUAL"
    HEDGE = "HEDGE"


class ShortType(str, Enum):
    """Why a short exists. Systematic shorts are optimizer-generated hedges;
    discretionary shorts are human ideas. These are NEVER conflated (invariant I-03),
    and the optimizer never mutates a discretionary short (I-04)."""

    SYSTEMATIC = "SYSTEMATIC"        # optimizer hedge — neutralizes long-book beta
    DISCRETIONARY = "DISCRETIONARY"  # PM/analyst idea — an input to the optimizer
    NA = "NA"                        # not applicable (long positions)


class BookType(str, Enum):
    """The three books P&L is reported across — never conflated (invariant I-03)."""

    LONG = "LONG"
    SYSTEMATIC_SHORT = "SYSTEMATIC_SHORT"
    DISCRETIONARY_SHORT = "DISCRETIONARY_SHORT"


class Mandate(str, Enum):
    """A portfolio's shorting mandate. LONG_SHORT books carry a hedge and must hold to the
    net-beta constraint; LONG_ONLY books may NOT short at all (no systematic hedge, no
    discretionary short) and are intentionally long-beta — so they're carved OUT of the
    consolidated beta-balance check (their exposure is expected, not a breach)."""

    LONG_SHORT = "LONG_SHORT"
    LONG_ONLY = "LONG_ONLY"


@dataclass
class LotEntry:
    """A position's open lot as carried on the domain object (mirrors pnl.Lot).

    ``entry_price`` is the clean execution price; ``fee_per_share`` is the per-share
    transaction cost. Realized cost basis = execution + fee (long) / − fee (short)."""

    quantity: float
    entry_price: float
    entry_date: date
    fee_per_share: float = 0.0


@dataclass
class Position:
    """A single book position.

    ``notional`` is always a positive dollar magnitude; direction is given by
    ``side``. ``forward_beta``, when set by a PM, supersedes the entire beta pipeline
    for this position (post-catalyst override).

    ``lots`` / ``cost_basis_method`` / ``realised_pnl`` drive the P&L engine; they are
    additive to ``notional`` (which remains authoritative for beta/factor risk).

    ``thesis`` and ``target`` belong to the Fundamental Layer: Neptune reads them but
    must never write or auto-generate them.
    """

    ticker: str
    side: Side
    notional: float
    short_type: ShortType = ShortType.NA
    forward_beta: float | None = None
    sector: str | None = None
    cost_basis_method: CostBasisMethod | None = None
    realised_pnl: float = 0.0
    lots: list[LotEntry] = field(default_factory=list)
    # --- Per-name coverage (firm staff; see domain/org.py) ---
    # PM/analyst assigned to THIS name. Both optional: if pm_id is unset, the effective PM
    # is the book's lead PM. These reference Person ids; they never carry investment thesis.
    pm_id: str | None = None
    analyst_id: str | None = None
    # --- Fundamental Layer (read-only to the system) ---
    thesis: str | None = None
    target: str | None = None
    # Persistence id, populated when read back from the DB (None for in-memory positions).
    id: int | None = None

    @property
    def quantity(self) -> float:
        """Total open quantity across lots (0 for notional-only positions)."""
        return sum(l.quantity for l in self.lots)

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

    @property
    def direction(self) -> int:
        """+1 for long, -1 for short — the sign convention the P&L engine expects."""
        return 1 if self.side is Side.LONG else -1

    @property
    def book(self) -> BookType:
        """Which of the three reporting books this position belongs to."""
        if self.side is Side.LONG:
            return BookType.LONG
        if self.short_type is ShortType.SYSTEMATIC:
            return BookType.SYSTEMATIC_SHORT
        return BookType.DISCRETIONARY_SHORT


@dataclass
class Portfolio:
    """A book of positions belonging to one investor entity.

    Ownership (all optional so existing call sites and the synthetic slice keep working):
    ``investor_entity_id`` is the client account the book runs for; ``firm_id`` is the
    managing firm (the tenant); ``lead_pm_ids`` are the book's lead PM(s) — usually one,
    but co-PMs are supported. People are firm staff (see ``domain/org.py``)."""

    id: str
    name: str
    base_currency: str = "USD"
    is_paper: bool = False
    firm_id: str | None = None
    investor_entity_id: str | None = None
    lead_pm_ids: list[str] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    mandate: Mandate = Mandate.LONG_SHORT  # LONG_ONLY books may not short (see Mandate)

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


@dataclass(frozen=True)
class Transaction:
    """A booked execution (one blotter row). Read model returned by the ledger; the system
    records these but never routes them to a venue (CLAUDE.md §2)."""

    ticker: str
    action: TradeAction
    quantity: float
    price: float
    trade_date: date
    short_type: ShortType = ShortType.NA
    origin: TradeOrigin = TradeOrigin.MANUAL
    realized_pnl: float = 0.0
    effect: str = ""
    fee_per_share: float = 0.0
    portfolio_id: str | None = None
    executed_at: datetime | None = None
    id: int | None = None

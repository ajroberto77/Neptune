"""Organizational domain types: the people and entities that own books.

Two distinct entity concepts that must never be confused:

* ``ManagementFirm`` — the investment-management firm (e.g. Iridium). This is the
  **tenant / isolation root**: a subscriber to a public version would be another firm.
  PMs and analysts are employed by the FIRM.
* ``InvestorEntity`` — a client/account whose capital the firm manages. One firm can
  manage several investor entities; a ``book`` (portfolio) belongs to one of them.

``Person`` (PM / Analyst / CIO / Admin) belongs to the *firm*, not the client. A book has
one or more lead PMs (co-PMs supported via the ``is_lead`` flag on the book↔person link),
and each position may name a PM and an analyst for that specific name.

These are plain dataclasses with no persistence coupling, mirroring ``domain/models.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PersonRole(str, Enum):
    """A firm person's role. Aligns with the roadmap's CIO/PM/Analyst/Admin RBAC."""

    CIO = "CIO"
    PM = "PM"
    ANALYST = "ANALYST"
    ADMIN = "ADMIN"


@dataclass
class ManagementFirm:
    """The investment-management firm — the tenant / isolation root."""

    id: str
    name: str
    is_internal: bool = False        # True for Iridium's own firm row
    subscription_tier: str | None = None


@dataclass
class Person:
    """A PM / analyst / CIO / admin employed by a ``ManagementFirm``."""

    id: str
    firm_id: str
    name: str
    role: PersonRole
    email: str | None = None
    is_active: bool = True


@dataclass
class InvestorEntity:
    """A client/account whose capital the firm manages. Books belong to one of these."""

    id: str
    firm_id: str
    name: str
    base_currency: str = "USD"


@dataclass
class BookManager:
    """A book↔person management link. ``is_lead`` marks lead PM(s); co-PMs are >1 lead."""

    person_id: str
    is_lead: bool = True

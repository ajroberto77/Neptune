"""Ownership / multi-tenancy tests: management firm → person, investor entity → book,
per-name PM/analyst attribution, and the lead-PM fallback (in-memory SQLite)."""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from neptune.domain.models import Position, Side, ShortType
from neptune.domain.org import PersonRole
from neptune.positions.service import PositionService


def _firm_with_people(service: PositionService) -> None:
    service.create_firm("IRIDIUM", "Iridium Capital Management", is_internal=True)
    service.create_person("pm1", "IRIDIUM", "Lead PM", PersonRole.PM)
    service.create_person("pm2", "IRIDIUM", "Co PM", PersonRole.PM)
    service.create_person("an1", "IRIDIUM", "Analyst One", PersonRole.ANALYST)
    service.create_investor_entity("FUND", "IRIDIUM", "Iridium Master Fund")


def test_book_belongs_to_entity_with_lead_pm(session):
    service = PositionService(session)
    _firm_with_people(service)
    service.create_portfolio(
        "B1", "Tech Book", firm_id="IRIDIUM", investor_entity_id="FUND",
        lead_pm_ids=["pm1"],
    )
    book = service.get_portfolio("B1")
    assert book.firm_id == "IRIDIUM"
    assert book.investor_entity_id == "FUND"
    assert book.lead_pm_ids == ["pm1"]


def test_co_pms_supported(session):
    """A book may have more than one lead PM (co-PMs)."""
    service = PositionService(session)
    _firm_with_people(service)
    service.create_portfolio("B2", "Co-managed", firm_id="IRIDIUM",
                             investor_entity_id="FUND", lead_pm_ids=["pm1", "pm2"])
    assert set(service.get_portfolio("B2").lead_pm_ids) == {"pm1", "pm2"}


def test_position_pm_and_analyst_round_trip(session):
    service = PositionService(session)
    _firm_with_people(service)
    service.create_portfolio("B3", "Book", firm_id="IRIDIUM",
                             investor_entity_id="FUND", lead_pm_ids=["pm1"])
    service.add_position(
        "B3", Position("AAA", Side.LONG, 1_000_000, forward_beta=1.0,
                       pm_id="pm2", analyst_id="an1")
    )
    [p] = service.list_positions("B3")
    assert p.pm_id == "pm2"
    assert p.analyst_id == "an1"


def test_effective_pm_falls_back_to_book_lead(session):
    """A name with no pm_id is covered by the book's lead PM; an explicit pm_id wins."""
    service = PositionService(session)
    _firm_with_people(service)
    service.create_portfolio("B4", "Book", firm_id="IRIDIUM",
                             investor_entity_id="FUND", lead_pm_ids=["pm1"])
    no_override = Position("AAA", Side.LONG, 100, forward_beta=1.0)
    override = Position("BBB", Side.LONG, 100, forward_beta=1.0, pm_id="pm2")
    assert service.effective_pm("B4", no_override) == "pm1"
    assert service.effective_pm("B4", override) == "pm2"


def test_effective_pm_none_when_unassigned(session):
    """The bare synthetic slice (book with no lead PM) yields no effective PM."""
    service = PositionService(session)
    service.create_portfolio("B5", "Bare Book")
    assert service.effective_pm("B5", Position("AAA", Side.LONG, 100, forward_beta=1.0)) is None


def test_dangling_pm_id_is_rejected(session):
    """A position.pm_id with no matching people row is a FK violation — enforced on both
    SQLite (via PRAGMA foreign_keys=ON) and Postgres."""
    service = PositionService(session)
    service.create_portfolio("B6", "Book")
    with pytest.raises(IntegrityError):
        service.add_position(
            "B6", Position("AAA", Side.LONG, 100, forward_beta=1.0, pm_id="ghost")
        )

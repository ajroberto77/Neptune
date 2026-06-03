"""Macro-data layer: registry seed, the append-only revision/vintage logic, MARKET
observations, and the risk-layer transforms (``docs/macro_data.md``)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from neptune.macro import repository as repo
from neptune.macro.catalog import PHASE1_CATALOG, seed_catalog
from neptune.macro.models import (
    Frequency,
    SeriesClass,
    TransformHorizon,
    TransformOp,
    ValueType,
)
from neptune.risk.macro_transforms import (
    TransformError,
    annualize,
    apply_transform,
    validate_transform,
)


# --- 1c: registry seed + type flags ----------------------------------------------


def test_seed_catalog_idempotent_and_typed(macro_session):
    n = seed_catalog(macro_session)
    macro_session.commit()
    assert n == len(PHASE1_CATALOG)
    # Re-seeding doesn't duplicate (upsert keyed on series_id).
    seed_catalog(macro_session)
    macro_session.commit()
    assert len(repo.list_series(macro_session)) == len(PHASE1_CATALOG)

    cpi = repo.get_series(macro_session, "CPI_HEADLINE")
    assert cpi.series_class is SeriesClass.ECON
    assert cpi.is_vintaged is True            # ECON ⇒ point-in-time
    assert cpi.value_type is ValueType.INDEX  # an index → YoY is a PCT_CHANGE
    assert cpi.transform_op is TransformOp.PCT_CHANGE
    assert cpi.transform_horizon is TransformHorizon.YOY

    wti = repo.get_series(macro_session, "WTI")
    assert wti.series_class is SeriesClass.MARKET
    assert wti.is_vintaged is False           # MARKET ⇒ flat, not revised
    assert wti.value_type is ValueType.PRICE  # a price → LOG_DIFF

    # Every MARKET series is flat; every ECON series is vintaged.
    for s in repo.list_series(macro_session):
        assert s.is_vintaged == (s.series_class is SeriesClass.ECON)


# --- 1b: ECON vintages — revision / restatement / point-in-time -------------------


def test_revision_latest_first_print_and_as_of(macro_session):
    seed_catalog(macro_session)
    ref = date(2009, 3, 1)  # 2009 Q1
    # advance → third estimate → a 2014 benchmark restatement, each a NEW vintage row.
    repo.record_vintage(macro_session, "GDP", ref, date(2009, 4, 29), -6.4)
    repo.record_vintage(macro_session, "GDP", ref, date(2009, 6, 25), -5.5)
    repo.record_vintage(macro_session, "GDP", ref, date(2014, 7, 30), -4.6)
    macro_session.commit()

    assert repo.latest(macro_session, "GDP") == [(ref, -4.6)]
    assert repo.first_print(macro_session, "GDP") == [(ref, -6.4)]
    # Point-in-time: only vintages knowable by the as-of date.
    assert repo.as_of(macro_session, "GDP", date(2009, 4, 1)) == []        # before advance
    assert repo.as_of(macro_session, "GDP", date(2009, 5, 1)) == [(ref, -6.4)]
    assert repo.as_of(macro_session, "GDP", date(2009, 7, 1)) == [(ref, -5.5)]
    assert repo.as_of(macro_session, "GDP", date(2015, 1, 1)) == [(ref, -4.6)]


def test_as_of_reconstructs_the_panel(macro_session):
    seed_catalog(macro_session)
    q1, q2 = date(2009, 3, 1), date(2009, 6, 1)
    repo.record_vintage(macro_session, "GDP", q1, date(2009, 4, 29), -6.4)
    repo.record_vintage(macro_session, "GDP", q2, date(2009, 7, 31), -0.7)
    macro_session.commit()
    # On 2009-07-01 only Q1 was published; Q2 prints later.
    assert repo.as_of(macro_session, "GDP", date(2009, 7, 1)) == [(q1, -6.4)]
    assert repo.latest(macro_session, "GDP") == [(q1, -6.4), (q2, -0.7)]


def test_benchmark_restatement_batch(macro_session):
    seed_catalog(macro_session)
    # First prints across three months.
    for m, v in [(1, 100.0), (2, 101.0), (3, 102.0)]:
        repo.record_vintage(macro_session, "CPI_HEADLINE", date(2020, m, 1), date(2020, m + 1, 10), v)
    # An annual benchmark on one vintage_date rewrites all three months at once.
    bench = date(2021, 2, 12)
    n = repo.record_vintage_batch(
        macro_session, "CPI_HEADLINE", bench,
        {date(2020, 1, 1): 100.3, date(2020, 2, 1): 101.4, date(2020, 3, 1): 102.2},
    )
    macro_session.commit()
    assert n == 3
    # Old prints survive (first_print) while latest reflects the restatement.
    assert dict(repo.first_print(macro_session, "CPI_HEADLINE"))[date(2020, 1, 1)] == 100.0
    assert dict(repo.latest(macro_session, "CPI_HEADLINE"))[date(2020, 1, 1)] == 100.3
    # As-of just before the benchmark still shows the originals.
    asof_pre = dict(repo.as_of(macro_session, "CPI_HEADLINE", date(2021, 1, 1)))
    assert asof_pre[date(2020, 1, 1)] == 100.0


def test_ingest_latest_builds_vintages_by_diffing(macro_session):
    seed_catalog(macro_session)
    ref = date(2024, 1, 1)
    # First sight of the value → writes a vintage dated the sync date.
    assert repo.ingest_latest(macro_session, "UNRATE", ref, 3.7, date(2024, 2, 2)) is True
    # Unchanged on the next sync → no new row.
    assert repo.ingest_latest(macro_session, "UNRATE", ref, 3.7, date(2024, 2, 3)) is False
    # A revision → a new vintage row, old one preserved.
    assert repo.ingest_latest(macro_session, "UNRATE", ref, 3.9, date(2024, 3, 8)) is True
    macro_session.commit()
    assert repo.first_print(macro_session, "UNRATE") == [(ref, 3.7)]
    assert repo.latest(macro_session, "UNRATE") == [(ref, 3.9)]
    assert repo.as_of(macro_session, "UNRATE", date(2024, 2, 15)) == [(ref, 3.7)]


# --- 1a/1b: MARKET observations (flat, upsert, multi-source) -----------------------


def test_market_observation_upsert_and_multisource(macro_session):
    seed_catalog(macro_session)
    d = date(2026, 6, 1)
    repo.record_observation(macro_session, "UST_10Y", d, 4.21)
    repo.record_observation(macro_session, "UST_10Y", d, 4.25)  # vendor correction → upsert
    repo.record_observation(macro_session, "UST_10Y", d, 4.25, source="H15")  # other source coexists
    repo.record_observation(macro_session, "UST_10Y", date(2026, 6, 2), 4.30)
    macro_session.commit()
    assert repo.observations(macro_session, "UST_10Y", source="FRED") == [(d, 4.25), (date(2026, 6, 2), 4.30)]
    assert repo.observations(macro_session, "UST_10Y", source="H15") == [(d, 4.25)]


# --- 1c: risk-layer transforms (the YoY-of-a-rate vs YoY-of-a-price distinction) ---


def test_cpi_yoy_is_pct_change():
    cpi = [100.0] * 12 + [103.0]  # 13 monthly index levels
    out = apply_transform(
        cpi, value_type=ValueType.INDEX, op=TransformOp.PCT_CHANGE,
        horizon=TransformHorizon.YOY, frequency=Frequency.MONTHLY,
    )
    assert out == pytest.approx([0.03])  # +3% inflation


def test_unemployment_yoy_is_a_point_difference():
    unrate = [3.6] * 12 + [4.1]
    out = apply_transform(
        unrate, value_type=ValueType.RATE, op=TransformOp.DIFF,
        horizon=TransformHorizon.YOY, frequency=Frequency.MONTHLY,
    )
    assert out == pytest.approx([0.5])  # +0.5pp, NOT a percent change


def test_payems_diff_pop_is_new_jobs():
    payems = [150_000.0, 150_142.0, 150_200.0]
    out = apply_transform(
        payems, value_type=ValueType.LEVEL, op=TransformOp.DIFF,
        horizon=TransformHorizon.POP, frequency=Frequency.MONTHLY,
    )
    assert out == pytest.approx([142.0, 58.0])  # monthly "new jobs"


def test_validation_refuses_invalid_transforms():
    # You never percent-change a rate.
    with pytest.raises(TransformError):
        validate_transform(ValueType.RATE, TransformOp.PCT_CHANGE)
    # You never re-transform an already-differenced series.
    with pytest.raises(TransformError):
        validate_transform(ValueType.RATE_OF_CHANGE, TransformOp.DIFF)
    # A diffusion index is read as-is.
    with pytest.raises(TransformError):
        validate_transform(ValueType.DIFFUSION, TransformOp.PCT_CHANGE)
    # A price percent-change is fine (oil/FX YoY is a genuine operation).
    validate_transform(ValueType.PRICE, TransformOp.PCT_CHANGE)
    validate_transform(ValueType.INDEX, TransformOp.LOG_DIFF)


def test_annualize_quarterly_growth():
    out = annualize([0.01], Frequency.QUARTERLY)  # 1% QoQ
    assert out == pytest.approx([(1.01) ** 4 - 1.0])


def test_apply_transform_none_is_identity_and_short_series_safe():
    vals = [1.0, 2.0, 3.0]
    assert list(apply_transform(vals, value_type=ValueType.RATE, op=TransformOp.NONE,
                                horizon=TransformHorizon.NONE, frequency=Frequency.MONTHLY)) == vals
    # YoY on too-short a series yields empty, never a fabricated value.
    short = apply_transform([100.0, 101.0], value_type=ValueType.INDEX, op=TransformOp.PCT_CHANGE,
                            horizon=TransformHorizon.YOY, frequency=Frequency.MONTHLY)
    assert short.size == 0


# --- layer purity (CLAUDE.md §1): the pure engine never imports the macro DB --------


def _import_lines(pkg: str) -> list[str]:
    """Actual import statements across a package's source (ignores prose/docstrings)."""
    root = Path(__file__).resolve().parents[1] / "src" / "neptune" / pkg
    lines: list[str] = []
    for p in root.rglob("*.py"):
        for raw in p.read_text().splitlines():
            s = raw.strip()
            if s.startswith(("import ", "from ")):
                lines.append(s)
    return lines


def test_quant_engine_does_not_import_macro():
    assert not [ln for ln in _import_lines("quant") if "neptune.macro" in ln]


def test_macro_layer_does_not_import_quant_or_network():
    imports = _import_lines("macro")
    # Data layer must not depend on the engine, nor (in 1a–1c) make network calls.
    assert not [ln for ln in imports if "neptune.quant" in ln]
    forbidden = ("requests", "httpx", "urllib.request", "urllib.error", "socket", "aiohttp")
    bad = [ln for ln in imports if any(mod in ln for mod in forbidden)]
    assert not bad, f"unexpected network import in macro layer: {bad}"

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


def _import_lines(*rel_paths: str) -> list[str]:
    """Actual import statements across the given source files/dirs (ignores prose/docstrings)."""
    base = Path(__file__).resolve().parents[1] / "src" / "neptune"
    files: list[Path] = []
    for rel in rel_paths:
        p = base / rel
        files.extend(p.rglob("*.py") if p.is_dir() else [p])
    lines: list[str] = []
    for p in files:
        for raw in p.read_text().splitlines():
            s = raw.strip()
            if s.startswith(("import ", "from ")):
                lines.append(s)
    return lines


def test_quant_engine_does_not_import_macro():
    assert not [ln for ln in _import_lines("quant") if "neptune.macro" in ln]


def test_macro_layer_does_not_import_quant():
    # The whole data layer is independent of the pure engine.
    assert not [ln for ln in _import_lines("macro") if "neptune.quant" in ln]


def test_macro_core_is_network_free():
    # The storage/read core stays network-free; only providers/ingest may do I/O.
    core = _import_lines("macro/models.py", "macro/repository.py", "macro/catalog.py")
    forbidden = ("requests", "httpx", "urllib.request", "urllib.error", "socket", "aiohttp")
    bad = [ln for ln in core if any(mod in ln for mod in forbidden)]
    assert not bad, f"unexpected network import in macro core: {bad}"


# --- 1d: ingest (mocked provider — no live network) -------------------------------


class _FakeProvider:
    def __init__(self, obs=None, vints=None):
        self._obs = obs or {}
        self._vints = vints or {}

    def observations(self, code, *, start=None, end=None):
        return self._obs.get(code, [])

    def vintage_observations(self, code, *, start=None, end=None):
        return self._vints.get(code, [])


def test_ingest_market_and_econ_series(macro_session):
    from neptune.macro.ingest import ingest_series
    from neptune.macro.providers import ObservationPoint, VintagePoint

    seed_catalog(macro_session)
    prov = _FakeProvider(
        obs={"DGS10": [ObservationPoint(date(2024, 1, 2), 3.95),
                       ObservationPoint(date(2024, 1, 3), 4.00)]},
        vints={"GDPC1": [VintagePoint(date(2024, 1, 1), date(2024, 4, 25), 1.6),
                         VintagePoint(date(2024, 1, 1), date(2024, 6, 27), 1.4)]},
    )
    assert ingest_series(macro_session, repo.get_series(macro_session, "UST_10Y"), prov) == 2
    assert repo.observations(macro_session, "UST_10Y") == [
        (date(2024, 1, 2), 3.95), (date(2024, 1, 3), 4.00)]

    assert ingest_series(macro_session, repo.get_series(macro_session, "GDP"), prov) == 2
    # ECON lands as point-in-time vintages: latest vs first-print both recoverable.
    assert repo.latest(macro_session, "GDP") == [(date(2024, 1, 1), 1.4)]
    assert repo.first_print(macro_session, "GDP") == [(date(2024, 1, 1), 1.6)]
    macro_session.commit()


def test_ingest_skips_series_without_free_code(macro_session):
    from neptune.macro.ingest import ingest_series

    seed_catalog(macro_session)
    # ISM PMI has source 'ISM' and no source_code → nothing to pull.
    assert ingest_series(macro_session, repo.get_series(macro_session, "ISM_MFG"), _FakeProvider()) == 0
    # SHORT_RATE is derived (source='derived') → never ingested.
    assert ingest_series(macro_session, repo.get_series(macro_session, "SHORT_RATE"), _FakeProvider()) == 0


def test_ff_target_merges_two_fred_codes_with_later_winning(macro_session):
    from neptune.macro.ingest import ingest_series
    from neptune.macro.providers import ObservationPoint

    seed_catalog(macro_session)
    # FF_TARGET source_code = "DFEDTAR,DFEDTARU": pre-2008 single target then range upper.
    prov = _FakeProvider(obs={
        "DFEDTAR": [ObservationPoint(date(2007, 1, 1), 5.25),
                    ObservationPoint(date(2008, 12, 16), 1.00)],   # last single-target day
        "DFEDTARU": [ObservationPoint(date(2008, 12, 16), 0.25),    # range begins — wins on overlap
                     ObservationPoint(date(2009, 1, 1), 0.25)],
    })
    n = ingest_series(macro_session, repo.get_series(macro_session, "FF_TARGET"), prov)
    macro_session.commit()
    assert n == 3  # 2007-01-01, 2008-12-16 (deduped), 2009-01-01
    obs = dict(repo.observations(macro_session, "FF_TARGET"))
    assert obs[date(2007, 1, 1)] == 5.25
    assert obs[date(2008, 12, 16)] == 0.25  # the later code (DFEDTARU) wins at the boundary
    assert obs[date(2009, 1, 1)] == 0.25


def test_ingest_catalog_only_subset_is_idempotent(macro_session):
    from neptune.macro.ingest import ingest_catalog
    from neptune.macro.providers import ObservationPoint

    seed_catalog(macro_session)
    prov = _FakeProvider(obs={"DGS10": [ObservationPoint(date(2024, 1, 2), 3.95)]})
    counts = ingest_catalog(macro_session, prov, only={"UST_10Y"})
    assert counts == {"UST_10Y": 1}
    # Re-running upserts, not duplicates.
    ingest_catalog(macro_session, prov, only={"UST_10Y"})
    assert repo.observations(macro_session, "UST_10Y") == [(date(2024, 1, 2), 3.95)]


# --- 1d: FRED provider parsing (fake session — no live network) -------------------


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return _FakeResp(self._payload)


def test_fred_provider_parses_and_skips_missing():
    from neptune.macro.providers import FredProvider, ObservationPoint

    payload = {"observations": [
        {"date": "2024-01-02", "value": "3.95"},
        {"date": "2024-01-03", "value": "."},  # missing → skipped
    ]}
    sess = _FakeSession(payload)
    prov = FredProvider("KEY", session=sess)
    assert prov.observations("DGS10") == [ObservationPoint(date(2024, 1, 2), 3.95)]
    # api_key + file_type were injected into the query.
    _url, params = sess.calls[0]
    assert params["api_key"] == "KEY" and params["file_type"] == "json"


def test_short_rate_splice_is_spread_adjusted():
    from neptune.risk.macro_derive import splice

    effr = [(date(2018, 1, 1), 1.40), (date(2018, 3, 1), 1.50), (date(2018, 5, 1), 1.70)]
    sofr = [(date(2018, 4, 2), 1.75), (date(2018, 5, 1), 1.74)]
    # Overlap = 2018-05-01 → spread = 1.74 − 1.70 = 0.04; pre-join EFFR shifted +0.04.
    out = dict(splice(effr, sofr, join=date(2018, 4, 2)))
    assert out[date(2018, 1, 1)] == pytest.approx(1.44)
    assert out[date(2018, 3, 1)] == pytest.approx(1.54)
    assert out[date(2018, 4, 2)] == 1.75            # from SOFR
    assert out[date(2018, 5, 1)] == 1.74            # SOFR, not the EFFR 1.70
    # Plain concatenation (no adjustment) leaves the level step.
    raw = dict(splice(effr, sofr, join=date(2018, 4, 2), spread_adjust=False))
    assert raw[date(2018, 1, 1)] == 1.40


def test_short_rate_reads_from_the_macro_db(macro_session):
    from neptune.risk.macro_derive import short_rate

    seed_catalog(macro_session)
    for d, v in [(date(2018, 1, 1), 1.40), (date(2018, 5, 1), 1.70)]:
        repo.record_observation(macro_session, "FEDFUNDS", d, v)
    for d, v in [(date(2018, 4, 2), 1.75), (date(2018, 5, 1), 1.74)]:
        repo.record_observation(macro_session, "SOFR", d, v)
    macro_session.commit()
    out = dict(short_rate(macro_session))
    assert out[date(2018, 1, 1)] == pytest.approx(1.44)  # EFFR + 0.04 overlap spread
    assert out[date(2018, 5, 1)] == 1.74                 # SOFR takes over from the join


def test_fred_provider_maps_realtime_start_to_vintage_date():
    from neptune.macro.providers import FredProvider, VintagePoint

    payload = {"observations": [
        {"date": "2009-03-01", "value": "-6.4", "realtime_start": "2009-04-29"},
        {"date": "2009-03-01", "value": "-5.5", "realtime_start": "2009-06-25"},
    ]}
    prov = FredProvider("KEY", session=_FakeSession(payload))
    assert prov.vintage_observations("GDPC1") == [
        VintagePoint(date(2009, 3, 1), date(2009, 4, 29), -6.4),
        VintagePoint(date(2009, 3, 1), date(2009, 6, 25), -5.5),
    ]

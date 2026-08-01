"""Derived macro series — values composed on read from raw inputs, never stored.

Per the design (raw stays canonical in the fact tables; derivations live in the risk layer),
a *derived* series like the continuous short-term funding rate is built here from the raw
ingested inputs, not materialized into the DB.

``SHORT_RATE`` = a spread-adjusted splice of FEDFUNDS (EFFR, the unsecured overnight rate,
long history) and SOFR (the secured repo rate, published only from 2018-04). Because the two
are *different* rates, a naïve concatenation would put an artificial step at the join equal to
that day's SOFR−EFFR spread; we instead shift the pre-join EFFR by the mean overlap spread so
the level is continuous. (The adjustment is a couple of bp, but it keeps any change/z-score
transform honest.)
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy.orm import Session

from neptune.macro import repository as repo

# First published SOFR (NY Fed). Before this, EFFR carries the series.
SOFR_START = date(2018, 4, 2)

Series = Sequence[tuple[date, float]]


def splice(old: Series, new: Series, *, join: date, spread_adjust: bool = True) -> list[tuple[date, float]]:
    """Splice two ``(date, value)`` series: ``old`` before ``join``, ``new`` from ``join`` on.
    When ``spread_adjust``, shift ``old`` by the mean ``new − old`` over their overlapping dates
    so there is no artificial level step at the join."""
    old_d = dict(old)
    new_d = dict(new)
    shift = 0.0
    if spread_adjust:
        overlap = sorted(set(old_d) & set(new_d))
        if overlap:
            shift = sum(new_d[d] - old_d[d] for d in overlap) / len(overlap)
    out: dict[date, float] = {}
    for d, v in old_d.items():
        if d < join:
            out[d] = v + shift
    for d, v in new_d.items():
        if d >= join:
            out[d] = v
    return sorted(out.items())


def short_rate(
    macro_sess: Session, *, spread_adjust: bool = True, join: date = SOFR_START
) -> list[tuple[date, float]]:
    """The continuous short-term funding rate, built from the raw FEDFUNDS (EFFR) and SOFR
    observations in the macro DB. Empty until those series are ingested."""
    effr = repo.observations(macro_sess, "FEDFUNDS")
    sofr = repo.observations(macro_sess, "SOFR")
    return splice(effr, sofr, join=join, spread_adjust=spread_adjust)

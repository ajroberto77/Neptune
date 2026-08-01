"""Risk-layer transforms for macro series (``docs/macro_data.md`` §2).

The fact tables store the **rawest canonical** value; the analysis-ready form (inflation,
"new jobs", annualized growth, z-score) is produced HERE, on read — never materialized into
the DB and never inside the pure Quant Engine.

"YoY" is a *horizon*, not an *operation*, and the operation depends on ``value_type``:

* a price **INDEX/LEVEL/PRICE** (CPI, oil, GBPUSD) → ``PCT_CHANGE``/``LOG_DIFF`` (a real
  two-point % change — inflation, a price return);
* an already-a-**RATE** (unemployment) → ``DIFF`` (a change in points; you never
  percent-change a rate);
* an already-differenced ``RATE_OF_CHANGE`` (or a ``DIFFUSION`` index) → ``NONE`` — don't
  transform it again.

``validate_transform`` enforces exactly that and raises on an invalid pairing, so a
mis-specified series can't silently produce a meaningless number.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from neptune.macro.models import (
    Frequency,
    TransformHorizon,
    TransformOp,
    ValueType,
)

# Operators that compute a relative change → valid only on price/level/index types.
_RELATIVE_OPS = {TransformOp.PCT_CHANGE, TransformOp.LOG_DIFF}
_LEVEL_LIKE = {ValueType.INDEX, ValueType.LEVEL, ValueType.PRICE}
# Types that already represent a change/level you read as-is — no operator may be applied.
_ALREADY_FINAL = {ValueType.RATE_OF_CHANGE, ValueType.DIFFUSION}

_YOY_PERIODS = {
    Frequency.DAILY: 252,
    Frequency.WEEKLY: 52,
    Frequency.MONTHLY: 12,
    Frequency.QUARTERLY: 4,
}


class TransformError(ValueError):
    """Raised when an operator is invalid for a series' ``value_type``."""


def validate_transform(value_type: ValueType, op: TransformOp) -> None:
    """Raise ``TransformError`` if ``op`` is not a legitimate operation for ``value_type``."""
    if op == TransformOp.NONE:
        return
    if value_type in _ALREADY_FINAL:
        raise TransformError(
            f"{value_type.value} is already analysis-ready; refusing to apply {op.value} "
            "(would double-transform)."
        )
    if op in _RELATIVE_OPS and value_type not in _LEVEL_LIKE:
        raise TransformError(
            f"{op.value} is invalid for {value_type.value}; a rate/spread/yield uses DIFF "
            "(a change in native units), not a percent change."
        )


def horizon_lag(horizon: TransformHorizon, frequency: Frequency) -> int:
    """Number of periods the operator spans. POP = 1; YOY depends on the frequency."""
    if horizon == TransformHorizon.NONE or horizon == TransformHorizon.POP:
        return 1
    return _YOY_PERIODS[frequency]


def apply_transform(
    values: Sequence[float],
    *,
    value_type: ValueType,
    op: TransformOp,
    horizon: TransformHorizon,
    frequency: Frequency,
) -> np.ndarray:
    """Apply ``op`` over ``horizon`` to ``values`` (oldest→newest), after validation.

    Change operators shorten the series by the lag (no fabricated leading values — a deliberate
    echo of the lesson that padding feeds bias into estimators). ``ANNUALIZE`` is composed by
    the caller via :func:`annualize`, not here.
    """
    validate_transform(value_type, op)
    arr = np.asarray(values, dtype=float)
    if op == TransformOp.NONE:
        return arr
    if op == TransformOp.ZSCORE:
        sd = arr.std()
        return (arr - arr.mean()) / sd if sd else np.zeros_like(arr)
    if op == TransformOp.ANNUALIZE:
        raise TransformError("ANNUALIZE is composed by the caller via annualize(); not a base op.")
    lag = horizon_lag(horizon, frequency)
    if arr.size <= lag:
        return np.empty(0, dtype=float)
    if op == TransformOp.DIFF:
        return arr[lag:] - arr[:-lag]
    if op == TransformOp.PCT_CHANGE:
        return arr[lag:] / arr[:-lag] - 1.0
    if op == TransformOp.LOG_DIFF:
        return np.log(arr[lag:]) - np.log(arr[:-lag])
    raise TransformError(f"unknown op {op!r}")


def annualize(period_growth: Sequence[float], frequency: Frequency) -> np.ndarray:
    """Annualize a period-over-period growth (e.g. QoQ → annualized): (1+g)^periods − 1."""
    periods = _YOY_PERIODS[frequency]
    g = np.asarray(period_growth, dtype=float)
    return (1.0 + g) ** periods - 1.0

"""Reproducible total-return price adjustment.

Reconstructs the split/dividend-adjusted close (``adj_close``) from raw closes plus the
corporate-action and dividend records — the CRSP/Yahoo back-adjustment. The most recent
bar is unadjusted (``adj_close == close``); each split or dividend scales all *strictly
earlier* bars:

* **Split** with multiplier ``s`` (2.0 for a 2:1 forward split): earlier prices divided by ``s``.
* **Dividend** of ``d`` per share, ex-date ``e``: earlier prices multiplied by
  ``(1 − d / C_{prev})`` where ``C_prev`` is the raw close on the trading day before ``e``.

We compute one cumulative factor from newest to oldest, so the result is exact and
reproducible from stored facts — adjustments are never destructive (raw ``close`` is kept).
This is used when a provider doesn't supply ``adj_close``; when it does, we trust the source.
"""
from __future__ import annotations

from datetime import date

from neptune.securities.providers import CorporateActionRecord, DividendRecord, PriceBar
from neptune.securities.models import CorporateActionType


def reconstruct_adj_close(
    bars: list[PriceBar],
    dividends: list[DividendRecord],
    corporate_actions: list[CorporateActionRecord],
) -> dict[date, float]:
    """Return ``{ts: adj_close}`` for every bar. Splits and dividends outside the bar
    date range are ignored (they can't be applied without the surrounding prices)."""
    ordered = sorted(bars, key=lambda b: b.ts)
    if not ordered:
        return {}
    closes = [b.close for b in ordered]
    dates = [b.ts for b in ordered]

    split_on = {
        c.effective_date: c.ratio
        for c in corporate_actions
        if c.action_type is CorporateActionType.SPLIT and c.ratio
    }
    div_on: dict[date, float] = {}
    for d in dividends:
        div_on[d.ex_date] = div_on.get(d.ex_date, 0.0) + d.amount

    adj: dict[date, float] = {}
    factor = 1.0  # product of event factors for events strictly after the current date
    for i in range(len(dates) - 1, -1, -1):
        t = dates[i]
        adj[t] = closes[i] * factor
        # Fold events effective ON date t into the factor — they affect older dates only.
        if t in split_on:
            factor /= split_on[t]
        if t in div_on and i > 0:
            c_prev = closes[i - 1]
            if c_prev > 0:
                factor *= 1.0 - div_on[t] / c_prev
    return adj

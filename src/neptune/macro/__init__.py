"""Neptune macro-data layer.

A Neptune-owned database for macro/economic series — market-observed financial data
(rates, credit spreads, breakevens, FX, vol, commodities) and released-and-revised
economic data (GDP, CPI, unemployment, payrolls, ISM). See ``docs/macro_data.md``.

This is a **data-layer input only** (CLAUDE.md §1): the pure Quant Engine
(``neptune.quant``) never imports it. Use is human-facing regime / scenario / stress
context only (§5) — it never mutates the beta pipeline or gives the short book a view.
"""

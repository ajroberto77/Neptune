"""Neptune securities (market-data) database.

This package owns the ``neptune_securities`` database: the **projection** of the shared
``cato_securities`` universe (read-only upstream truth, copied here as a thin local join
anchor) plus the time-series and reference facts Neptune ingests — prices, dividends,
corporate actions (splits etc.), and the trading calendar.

Strict separation (see ``docs/data_architecture.md``): nothing here writes back to the
universe; the universe is read read-only via ``neptune.universe`` and projected in.
"""

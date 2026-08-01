"""The Phase-1 macro indicator catalog — the registry as data (``docs/macro_data.md`` §6).

``seed_catalog(session)`` upserts these into ``macro_series``. Source codes are the FRED/
ALFRED mnemonics the Phase-1d ingest will pull; they are inert metadata here. ISM PMI has no
free FRED code (licensed), so it carries no ``source_code`` and a note.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from neptune.macro.models import Frequency as F
from neptune.macro.models import ObservationType as O
from neptune.macro.models import SeasonalAdjustment as SA
from neptune.macro.models import SeriesClass as C
from neptune.macro.models import Stationarity as St
from neptune.macro.models import TransformHorizon as H
from neptune.macro.models import TransformOp as Op
from neptune.macro.models import ValueType as V
from neptune.macro.repository import upsert_series

# Each entry is the full set of kwargs for ``upsert_series`` (minus session/series_id).
PHASE1_CATALOG: dict[str, dict] = {
    # --- MARKET: rates -----------------------------------------------------------
    "UST_3M": dict(series_class=C.MARKET, category="RATES", name="UST 3M CMT yield",
                   frequency=F.DAILY, units="percent", value_type=V.YIELD,
                   observation_type=O.STOCK, stationarity=St.STATIONARY,
                   source="FRED", source_code="DGS3MO"),
    "UST_2Y": dict(series_class=C.MARKET, category="RATES", name="UST 2Y CMT yield",
                   frequency=F.DAILY, units="percent", value_type=V.YIELD,
                   observation_type=O.STOCK, stationarity=St.STATIONARY,
                   source="FRED", source_code="DGS2"),
    "UST_10Y": dict(series_class=C.MARKET, category="RATES", name="UST 10Y CMT yield",
                    frequency=F.DAILY, units="percent", value_type=V.YIELD,
                    observation_type=O.STOCK, stationarity=St.STATIONARY,
                    source="FRED", source_code="DGS10"),
    "UST_30Y": dict(series_class=C.MARKET, category="RATES", name="UST 30Y CMT yield",
                    frequency=F.DAILY, units="percent", value_type=V.YIELD,
                    observation_type=O.STOCK, stationarity=St.STATIONARY,
                    source="FRED", source_code="DGS30"),
    "SPREAD_2s10s": dict(series_class=C.MARKET, category="RATES", name="2s10s curve slope",
                         frequency=F.DAILY, units="bp", value_type=V.SPREAD,
                         observation_type=O.STOCK, stationarity=St.STATIONARY,
                         source="FRED", source_code="T10Y2Y",
                         description="10Y minus 2Y; inversion = recession signal"),
    "SOFR": dict(series_class=C.MARKET, category="MONETARY", name="SOFR (overnight)",
                 frequency=F.DAILY, units="percent", value_type=V.RATE,
                 observation_type=O.STOCK, stationarity=St.STATIONARY,
                 source="FRED", source_code="SOFR"),
    "FEDFUNDS": dict(series_class=C.MARKET, category="MONETARY", name="Effective Fed Funds rate",
                     frequency=F.DAILY, units="percent", value_type=V.RATE,
                     observation_type=O.STOCK, stationarity=St.STATIONARY,
                     source="FRED", source_code="DFF"),
    # Policy TARGET (administered) — the cleanest monetary-regime marker (steps at FOMC dates).
    # Spliced from two FRED codes (same concept, code changed in 2008 when the single target
    # became a range): DFEDTAR (single, to 2008) then DFEDTARU (range upper, 2008→). Ingest
    # merges the ordered codes; later codes win at the boundary. No spread adjustment needed.
    "FF_TARGET": dict(series_class=C.MARKET, category="MONETARY",
                      name="Fed funds target (upper bound; pre-2008 single target)",
                      frequency=F.DAILY, units="percent", value_type=V.RATE,
                      observation_type=O.STOCK, stationarity=St.STATIONARY,
                      source="FRED", source_code="DFEDTAR,DFEDTARU",
                      description="Policy target: DFEDTAR (single→2008) spliced to DFEDTARU (range upper)"),
    # DERIVED continuous funding rate (NOT ingested): a spread-adjusted splice of FEDFUNDS
    # (EFFR, pre-2018-04) and SOFR (2018-04→), built on read by neptune.risk.macro_derive.
    # EFFR (unsecured) and SOFR (secured repo) are different rates, so the blend is a transparent
    # model choice — kept as a risk-layer derivation, not stored data.
    "SHORT_RATE": dict(series_class=C.MARKET, category="MONETARY",
                       name="Short-term funding rate (EFFR→SOFR splice, derived)",
                       frequency=F.DAILY, units="percent", value_type=V.RATE,
                       observation_type=O.STOCK, stationarity=St.STATIONARY,
                       source="derived", source_code=None,
                       description="Derived: spread-adjusted splice of FEDFUNDS (EFFR) + SOFR; not ingested"),
    # --- MARKET: credit ----------------------------------------------------------
    "IG_OAS": dict(series_class=C.MARKET, category="CREDIT", name="ICE BofA IG OAS",
                   frequency=F.DAILY, units="bp", value_type=V.SPREAD,
                   observation_type=O.STOCK, stationarity=St.STATIONARY,
                   source="FRED", source_code="BAMLC0A0CM"),
    "HY_OAS": dict(series_class=C.MARKET, category="CREDIT", name="ICE BofA HY OAS",
                   frequency=F.DAILY, units="bp", value_type=V.SPREAD,
                   observation_type=O.STOCK, stationarity=St.STATIONARY,
                   source="FRED", source_code="BAMLH0A0HYM2"),
    # --- MARKET: inflation (market-implied) / vol --------------------------------
    "BREAKEVEN_10Y": dict(series_class=C.MARKET, category="INFLATION",
                          name="10Y breakeven inflation", frequency=F.DAILY, units="percent",
                          value_type=V.RATE, observation_type=O.STOCK,
                          stationarity=St.STATIONARY, source="FRED", source_code="T10YIE"),
    "VIX": dict(series_class=C.MARKET, category="VOLATILITY", name="CBOE VIX",
                frequency=F.DAILY, units="index", value_type=V.INDEX,
                observation_type=O.STOCK, stationarity=St.STATIONARY,
                source="FRED", source_code="VIXCLS"),
    # --- MARKET: commodities (spot/front-month price levels) ---------------------
    "WTI": dict(series_class=C.MARKET, category="COMMODITY", name="WTI crude (spot)",
                frequency=F.DAILY, units="USD/bbl", value_type=V.PRICE,
                observation_type=O.STOCK, stationarity=St.NONSTATIONARY,
                transform_op=Op.LOG_DIFF, transform_horizon=H.POP,
                source="FRED", source_code="DCOILWTICO"),
    "BRENT": dict(series_class=C.MARKET, category="COMMODITY", name="Brent crude (spot)",
                  frequency=F.DAILY, units="USD/bbl", value_type=V.PRICE,
                  observation_type=O.STOCK, stationarity=St.NONSTATIONARY,
                  transform_op=Op.LOG_DIFF, transform_horizon=H.POP,
                  source="FRED", source_code="DCOILBRENTEU"),
    "GOLD": dict(series_class=C.MARKET, category="COMMODITY", name="LBMA gold price",
                 frequency=F.DAILY, units="USD/oz", value_type=V.PRICE,
                 observation_type=O.STOCK, stationarity=St.NONSTATIONARY,
                 transform_op=Op.LOG_DIFF, transform_horizon=H.POP,
                 source="FRED", source_code="GOLDAMGBD228NLBM"),
    "NATGAS": dict(series_class=C.MARKET, category="COMMODITY", name="Henry Hub natural gas",
                   frequency=F.DAILY, units="USD/MMBtu", value_type=V.PRICE,
                   observation_type=O.STOCK, stationarity=St.NONSTATIONARY,
                   transform_op=Op.LOG_DIFF, transform_horizon=H.POP,
                   source="FRED", source_code="DHHNGSP"),
    # --- MARKET: FX (price levels) -----------------------------------------------
    "DXY": dict(series_class=C.MARKET, category="FX", name="Trade-weighted USD index (broad)",
                frequency=F.DAILY, units="index", value_type=V.INDEX,
                observation_type=O.STOCK, stationarity=St.NONSTATIONARY,
                transform_op=Op.LOG_DIFF, transform_horizon=H.POP,
                source="FRED", source_code="DTWEXBGS"),
    "EURUSD": dict(series_class=C.MARKET, category="FX", name="EUR/USD", frequency=F.DAILY,
                   units="px", value_type=V.PRICE, observation_type=O.STOCK,
                   stationarity=St.NONSTATIONARY, transform_op=Op.LOG_DIFF,
                   transform_horizon=H.POP, source="FRED", source_code="DEXUSEU"),
    "USDJPY": dict(series_class=C.MARKET, category="FX", name="USD/JPY", frequency=F.DAILY,
                   units="px", value_type=V.PRICE, observation_type=O.STOCK,
                   stationarity=St.NONSTATIONARY, transform_op=Op.LOG_DIFF,
                   transform_horizon=H.POP, source="FRED", source_code="DEXJPUS"),
    "GBPUSD": dict(series_class=C.MARKET, category="FX", name="GBP/USD", frequency=F.DAILY,
                   units="px", value_type=V.PRICE, observation_type=O.STOCK,
                   stationarity=St.NONSTATIONARY, transform_op=Op.LOG_DIFF,
                   transform_horizon=H.POP, source="FRED", source_code="DEXUSUK"),
    # --- ECON: inflation (released & revised → vintaged) -------------------------
    "CPI_HEADLINE": dict(series_class=C.ECON, category="INFLATION", name="CPI-U (all items)",
                         frequency=F.MONTHLY, units="index", value_type=V.INDEX,
                         observation_type=O.STOCK, stationarity=St.NONSTATIONARY,
                         transform_op=Op.PCT_CHANGE, transform_horizon=H.YOY,
                         seasonal_adjustment=SA.SA, source="ALFRED", source_code="CPIAUCSL",
                         description="YoY %-change → headline inflation"),
    "CPI_CORE": dict(series_class=C.ECON, category="INFLATION", name="Core CPI (ex food/energy)",
                     frequency=F.MONTHLY, units="index", value_type=V.INDEX,
                     observation_type=O.STOCK, stationarity=St.NONSTATIONARY,
                     transform_op=Op.PCT_CHANGE, transform_horizon=H.YOY,
                     seasonal_adjustment=SA.SA, source="ALFRED", source_code="CPILFESL"),
    "PCE_CORE": dict(series_class=C.ECON, category="INFLATION", name="Core PCE price index",
                     frequency=F.MONTHLY, units="index", value_type=V.INDEX,
                     observation_type=O.STOCK, stationarity=St.NONSTATIONARY,
                     transform_op=Op.PCT_CHANGE, transform_horizon=H.YOY,
                     seasonal_adjustment=SA.SA, source="ALFRED", source_code="PCEPILFE",
                     description="The Fed's target inflation gauge"),
    # --- ECON: labor -------------------------------------------------------------
    "UNRATE": dict(series_class=C.ECON, category="LABOR", name="Unemployment rate",
                   frequency=F.MONTHLY, units="percent", value_type=V.RATE,
                   observation_type=O.STOCK, stationarity=St.STATIONARY,
                   transform_op=Op.NONE, transform_horizon=H.NONE,
                   seasonal_adjustment=SA.SA, source="ALFRED", source_code="UNRATE"),
    "PAYEMS": dict(series_class=C.ECON, category="LABOR", name="Nonfarm payrolls (level)",
                   frequency=F.MONTHLY, units="thousands", value_type=V.LEVEL,
                   observation_type=O.STOCK, stationarity=St.NONSTATIONARY,
                   transform_op=Op.DIFF, transform_horizon=H.POP,
                   seasonal_adjustment=SA.SA, source="ALFRED", source_code="PAYEMS",
                   description="MoM DIFF of the level = 'new jobs'"),
    "ICSA": dict(series_class=C.ECON, category="LABOR", name="Initial jobless claims",
                 frequency=F.WEEKLY, units="level", value_type=V.COUNT,
                 observation_type=O.FLOW, stationarity=St.STATIONARY,
                 transform_op=Op.NONE, transform_horizon=H.NONE,
                 seasonal_adjustment=SA.SA, source="ALFRED", source_code="ICSA"),
    # --- ECON: growth / activity -------------------------------------------------
    "GDP": dict(series_class=C.ECON, category="GROWTH", name="Real GDP (level)",
                frequency=F.QUARTERLY, units="$bn", value_type=V.LEVEL,
                observation_type=O.FLOW, stationarity=St.NONSTATIONARY,
                transform_op=Op.LOG_DIFF, transform_horizon=H.POP,
                seasonal_adjustment=SA.SA, source="ALFRED", source_code="GDPC1",
                description="QoQ log-change, annualized by the risk layer"),
    "ISM_MFG": dict(series_class=C.ECON, category="ACTIVITY", name="ISM Manufacturing PMI",
                    frequency=F.MONTHLY, units="index", value_type=V.DIFFUSION,
                    observation_type=O.NA, stationarity=St.STATIONARY,
                    transform_op=Op.NONE, transform_horizon=H.NONE,
                    seasonal_adjustment=SA.SA, source="ISM", source_code=None,
                    description="Diffusion (50 = neutral); licensed feed (no free FRED code)"),
}


def seed_catalog(session: Session) -> int:
    """Upsert the Phase-1 catalog into ``macro_series``. Idempotent; returns the count."""
    for series_id, kwargs in PHASE1_CATALOG.items():
        upsert_series(session, series_id, **kwargs)
    return len(PHASE1_CATALOG)

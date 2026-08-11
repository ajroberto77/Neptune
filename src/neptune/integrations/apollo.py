"""Fail-closed APOLLO optimizer-snapshot consumer.

This is deliberately a *consumer* adapter.  It performs no factor construction,
macro ingestion, beta estimation, or local silent fallback.  The input is the
versioned public APOLLO snapshot payload; Neptune adds only portfolio-owned
metadata (ticker/sector/variance) before passing candidates to its optimizer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from math import isfinite
from typing import Any, Mapping

import numpy as np

PUBLIC_SCHEMA_VERSION = "1.0"


class SnapshotUnavailable(RuntimeError):
    """No acceptable APOLLO snapshot exists for this hedge request."""


class SnapshotMode(StrEnum):
    APOLLO = "apollo"
    ACCEPTED_ROLLBACK = "accepted_rollback"


@dataclass(frozen=True)
class SnapshotRequest:
    factor_model_id: str
    factor_model_version: str
    as_of_date: date
    requested_at: datetime
    minimum_coverage: float = 0.90
    maximum_age: timedelta = timedelta(days=2)

    def __post_init__(self) -> None:
        if self.requested_at.utcoffset() is None:
            raise ValueError("Snapshot request timestamp must be timezone-aware.")
        if not 0 <= self.minimum_coverage <= 1:
            raise ValueError("Minimum coverage must be between zero and one.")
        if self.maximum_age < timedelta(0):
            raise ValueError("Maximum snapshot age cannot be negative.")


@dataclass(frozen=True)
class CandidateMetadata:
    entity_id: str
    ticker: str
    sector: str | None = None
    variance: float = 1.0

    def __post_init__(self) -> None:
        if not self.entity_id or not self.ticker:
            raise ValueError("Candidate entity and ticker are required.")
        if not isfinite(self.variance) or self.variance < 0:
            raise ValueError("Candidate variance must be finite and non-negative.")


@dataclass(frozen=True)
class OptimizerCandidateInput:
    """Neptune-owned candidate input ready for conversion by the optimizer boundary."""
    ticker: str
    beta: float
    loadings: Mapping[str, float]
    sector: str | None
    variance: float
    idio_var: float


@dataclass(frozen=True)
class ApolloRiskInputs:
    snapshot_id: str
    run_id: str
    factor_ids: tuple[str, ...]
    covariance: np.ndarray
    candidates: tuple[OptimizerCandidateInput, ...]
    provenance: Mapping[str, str]
    mode: SnapshotMode


@dataclass(frozen=True)
class MetricDifference:
    name: str
    local_value: float
    apollo_value: float
    absolute_difference: float
    tolerance: float

    @property
    def within_tolerance(self) -> bool:
        return self.absolute_difference <= self.tolerance


@dataclass(frozen=True)
class ShadowComparison:
    snapshot_id: str
    differences: tuple[MetricDifference, ...]

    @property
    def accepted(self) -> bool:
        return all(item.within_tolerance for item in self.differences)


def adapt_optimizer_snapshot(
    payload: Mapping[str, Any],
    request: SnapshotRequest,
    candidates: tuple[CandidateMetadata, ...],
    *,
    mode: SnapshotMode = SnapshotMode.APOLLO,
) -> ApolloRiskInputs:
    """Validate and translate one atomic APOLLO snapshot into optimizer inputs.

    A rollback snapshot is permitted only when it is explicitly marked accepted in
    its immutable provenance.  It is still an APOLLO snapshot, never a live local
    recalculation.
    """
    _require(payload, "schema_version", "snapshot_id", "run_id", "factor_model_id",
             "factor_model_version", "as_of_date", "knowledge_timestamp", "coverage_ratio",
             "model_members", "covariance", "exposures", "provenance")
    if payload["schema_version"] != PUBLIC_SCHEMA_VERSION:
        raise SnapshotUnavailable("Unsupported APOLLO snapshot schema version.")
    if payload["factor_model_id"] != request.factor_model_id or payload["factor_model_version"] != request.factor_model_version:
        raise SnapshotUnavailable("APOLLO snapshot model/version does not match the requested model.")
    if _date(payload["as_of_date"]) != request.as_of_date:
        raise SnapshotUnavailable("APOLLO snapshot as-of date does not match the request.")
    knowledge = _timestamp(payload["knowledge_timestamp"])
    if knowledge > request.requested_at:
        raise SnapshotUnavailable("APOLLO snapshot contains future knowledge.")
    if request.requested_at - knowledge > request.maximum_age:
        raise SnapshotUnavailable("APOLLO snapshot is stale for this hedge request.")
    if float(payload["coverage_ratio"]) < request.minimum_coverage:
        raise SnapshotUnavailable("APOLLO snapshot coverage is below the request minimum.")
    provenance = {str(k): str(v) for k, v in payload["provenance"].items()}
    if mode is SnapshotMode.ACCEPTED_ROLLBACK and provenance.get("rollback_accepted") != "true":
        raise SnapshotUnavailable("Rollback requires an explicitly accepted APOLLO snapshot.")

    members = tuple(str(item["factor_id"]) for item in payload["model_members"])
    if len(set(members)) != len(members):
        raise SnapshotUnavailable("APOLLO model members are not unique.")
    covariance = payload["covariance"]
    factor_ids = tuple(str(item) for item in covariance["factor_ids"])
    if factor_ids != ("MKT", *members):
        raise SnapshotUnavailable("APOLLO covariance order is incompatible with its factor model.")
    matrix = np.asarray(covariance["values"], dtype=float)
    if matrix.shape != (len(factor_ids), len(factor_ids)) or not np.isfinite(matrix).all():
        raise SnapshotUnavailable("APOLLO covariance payload is invalid.")
    if not np.allclose(matrix, matrix.T, atol=1e-10):
        raise SnapshotUnavailable("APOLLO covariance is not symmetric.")

    exposures = {str(item["entity_id"]): item for item in payload["exposures"]}
    if len(exposures) != len(payload["exposures"]):
        raise SnapshotUnavailable("APOLLO snapshot contains duplicate security exposures.")
    adapted: list[OptimizerCandidateInput] = []
    for metadata in candidates:
        exposure = exposures.get(metadata.entity_id)
        if exposure is None:
            raise SnapshotUnavailable(f"No APOLLO exposure for optimizer candidate {metadata.entity_id}.")
        if str(exposure.get("quality_status", "valid")) != "valid":
            raise SnapshotUnavailable(f"APOLLO exposure is not valid for {metadata.entity_id}.")
        if _date(exposure["as_of_date"]) != request.as_of_date:
            raise SnapshotUnavailable(f"APOLLO exposure as-of date does not match for {metadata.entity_id}.")
        exposure_time = _timestamp(exposure["knowledge_timestamp"])
        if exposure_time > knowledge:
            raise SnapshotUnavailable(f"APOLLO exposure is newer than its atomic snapshot for {metadata.entity_id}.")
        loadings = {str(key): float(value) for key, value in exposure.get("factor_loadings", {}).items()}
        if set(loadings) - set(members):
            raise SnapshotUnavailable(f"APOLLO exposure contains factors outside the model for {metadata.entity_id}.")
        beta, idio_var = float(exposure["beta"]), float(exposure["idiosyncratic_variance"])
        if not isfinite(beta) or not isfinite(idio_var) or idio_var < 0:
            raise SnapshotUnavailable(f"APOLLO exposure values are invalid for {metadata.entity_id}.")
        adapted.append(OptimizerCandidateInput(metadata.ticker, beta, loadings, metadata.sector, metadata.variance, idio_var))
    return ApolloRiskInputs(str(payload["snapshot_id"]), str(payload["run_id"]), factor_ids, matrix,
                             tuple(adapted), provenance, mode)


def compare_hedge_proposals(
    snapshot_id: str,
    local: Any,
    apollo: Any,
    *,
    beta_tolerance: float = 1e-6,
    factor_tolerance: float = 1e-6,
    weight_tolerance: float = 1e-5,
) -> ShadowComparison:
    """Produce explainable, non-adjustable shadow differences for review."""
    differences = [
        _difference("net_beta_after", local.net_beta_after, apollo.net_beta_after, beta_tolerance),
        _difference("tracking_error", local.tracking_error, apollo.tracking_error, factor_tolerance),
    ]
    for factor in sorted(set(local.factor_after) | set(apollo.factor_after)):
        differences.append(_difference(f"factor_after:{factor}", local.factor_after.get(factor, 0.0),
                                       apollo.factor_after.get(factor, 0.0), factor_tolerance))
    local_weights = {item.ticker: item.weight for item in local.positions}
    apollo_weights = {item.ticker: item.weight for item in apollo.positions}
    for ticker in sorted(set(local_weights) | set(apollo_weights)):
        differences.append(_difference(f"hedge_weight:{ticker}", local_weights.get(ticker, 0.0),
                                       apollo_weights.get(ticker, 0.0), weight_tolerance))
    return ShadowComparison(snapshot_id, tuple(differences))


def _difference(name: str, local: float, apollo: float, tolerance: float) -> MetricDifference:
    return MetricDifference(name, local, apollo, abs(local - apollo), tolerance)


def _require(payload: Mapping[str, Any], *keys: str) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise SnapshotUnavailable(f"APOLLO snapshot lacks required fields: {', '.join(missing)}.")


def _date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _timestamp(value: str | datetime) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise SnapshotUnavailable("APOLLO timestamps must be timezone-aware.")
    return result.astimezone(timezone.utc)

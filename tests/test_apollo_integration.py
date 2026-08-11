from datetime import date, datetime, timedelta, timezone
import unittest

from neptune.integrations.apollo import (
    CandidateMetadata, SnapshotMode, SnapshotRequest, SnapshotUnavailable,
    adapt_optimizer_snapshot, compare_hedge_proposals,
)

UTC = timezone.utc


def payload(*, knowledge="2026-01-31T16:00:00+00:00", coverage=1.0, rollback=False):
    return {
        "schema_version": "1.0", "snapshot_id": "apollo-risk-1", "run_id": "run-1",
        "factor_model_id": "neptune-risk", "factor_model_version": "1.0.0",
        "as_of_date": "2026-01-31", "knowledge_timestamp": knowledge, "coverage_ratio": coverage,
        "model_members": [{"factor_id": "SMB"}],
        "covariance": {"factor_ids": ["MKT", "SMB"], "values": [[.04, .01], [.01, .03]]},
        "exposures": [{"entity_id": "security-1", "beta": 1.2, "factor_loadings": {"SMB": .2},
                       "idiosyncratic_variance": .1, "as_of_date": "2026-01-31",
                       "knowledge_timestamp": knowledge, "quality_status": "valid"}],
        "provenance": {"rollback_accepted": "true" if rollback else "false"},
    }


class ApolloIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.request = SnapshotRequest("neptune-risk", "1.0.0", date(2026, 1, 31),
                                       datetime(2026, 1, 31, 17, tzinfo=UTC))
        self.candidates = (CandidateMetadata("security-1", "ABC", "Technology", .2),)

    def test_adapts_atomic_snapshot_without_local_factor_calculation(self):
        result = adapt_optimizer_snapshot(payload(), self.request, self.candidates)
        self.assertEqual(result.factor_ids, ("MKT", "SMB"))
        self.assertEqual(result.candidates[0].ticker, "ABC")
        self.assertAlmostEqual(result.candidates[0].idio_var, .1)

    def test_stale_or_missing_snapshot_fails_closed(self):
        with self.assertRaisesRegex(SnapshotUnavailable, "stale"):
            adapt_optimizer_snapshot(payload(knowledge="2026-01-20T16:00:00+00:00"), self.request, self.candidates)
        with self.assertRaisesRegex(SnapshotUnavailable, "No APOLLO exposure"):
            adapt_optimizer_snapshot(payload(), self.request, (CandidateMetadata("absent", "ZZZ"),))

    def test_rollback_requires_explicitly_accepted_snapshot(self):
        with self.assertRaisesRegex(SnapshotUnavailable, "explicitly accepted"):
            adapt_optimizer_snapshot(payload(), self.request, self.candidates, mode=SnapshotMode.ACCEPTED_ROLLBACK)
        result = adapt_optimizer_snapshot(payload(rollback=True), self.request, self.candidates,
                                          mode=SnapshotMode.ACCEPTED_ROLLBACK)
        self.assertEqual(result.mode, SnapshotMode.ACCEPTED_ROLLBACK)

    def test_shadow_comparison_exposes_weight_differences(self):
        class Short:
            def __init__(self, ticker, weight): self.ticker, self.weight = ticker, weight
        class Proposal:
            def __init__(self, weight):
                self.positions, self.net_beta_after, self.tracking_error, self.factor_after = [Short("ABC", weight)], .0, .0, {"SMB": .0}
        local, apollo = Proposal(.10), Proposal(.11)
        report = compare_hedge_proposals("apollo-risk-1", local, apollo)
        self.assertFalse(report.accepted)
        self.assertIn("hedge_weight:ABC", [item.name for item in report.differences])

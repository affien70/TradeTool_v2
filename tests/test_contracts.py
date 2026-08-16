from __future__ import annotations

import unittest
from datetime import date

from tradetool.contracts.enums import CandidateType, CostBasisStatus, TradeSignal
from tradetool.contracts.models import (
    CandidateClassification,
    CoverageReport,
    HoldingsPosition,
    PriceHistoryRow,
    RankedCandidate,
)


class ContractTests(unittest.TestCase):
    def test_all_contract_models_import(self) -> None:
        from tradetool.contracts.models import (  # noqa: F401
            BenchmarkHistoryRow,
            CandidateClassification,
            ComparisonResult,
            CoverageReport,
            EligibilityResult,
            ExplanationResult,
            FeatureRow,
            HoldingsPosition,
            HoldingsSignalResult,
            MLArtifactMetadata,
            PriceHistoryRow,
            RankedCandidate,
            UniverseMember,
        )

    def test_candidate_type_enum_contains_exact_approved_values(self) -> None:
        self.assertEqual(
            [item.value for item in CandidateType],
            ['Stable Leader', 'Early Breakout', 'Extended Runner', 'Rebound Case', 'Reject'],
        )

    def test_ranking_and_trade_signal_fields_are_separate(self) -> None:
        ranked = RankedCandidate(
            ticker='NHY.OL',
            rank_date=date(2026, 8, 16),
            ranking_engine_id='baseline',
            raw_rank=1,
            raw_score=0.91,
        )
        classified = CandidateClassification(
            ticker='NHY.OL',
            rank_date=date(2026, 8, 16),
            candidate_type=CandidateType.STABLE_LEADER,
            trade_signal=TradeSignal.WATCH,
            classification_reasons=('separate policy layer',),
        )
        self.assertEqual(ranked.raw_rank, 1)
        self.assertEqual(classified.trade_signal, TradeSignal.WATCH)

    def test_invalid_ohlc_input_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PriceHistoryRow(
                ticker='NHY.OL',
                date=date(2026, 8, 16),
                open=10.0,
                high=9.0,
                low=8.0,
                close=8.5,
                volume=1000.0,
            )

    def test_non_positive_rank_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RankedCandidate(
                ticker='NHY.OL',
                rank_date=date(2026, 8, 16),
                ranking_engine_id='baseline',
                raw_rank=0,
                raw_score=0.1,
            )

    def test_rejected_or_ineligible_outcomes_require_reasons(self) -> None:
        from tradetool.contracts.models import EligibilityResult
        with self.assertRaises(ValueError):
            EligibilityResult(
                ticker='NHY.OL',
                feature_date=date(2026, 8, 16),
                eligible=False,
                rejection_reasons=(),
            )

    def test_coverage_counts_cannot_be_internally_impossible(self) -> None:
        with self.assertRaises(ValueError):
            CoverageReport(
                run_id='run-1',
                run_date=date(2026, 8, 16),
                input_universe_count=10,
                normalized_valid_ticker_count=9,
                market_data_coverage_count=9,
                enough_history_count=8,
                feature_complete_count=7,
                eligible_count=6,
                ranked_count=7,
                failed_count=1,
                rejection_counts_by_reason={'missing_data': 1},
                latest_data_date_distribution={'2026-08-15': 9},
            )

    def test_unknown_cost_basis_allows_null_average_cost(self) -> None:
        position = HoldingsPosition(
            ticker='NHY.OL',
            quantity=10.0,
            position_status='ACTIVE',
            cost_basis_status=CostBasisStatus.UNKNOWN,
            snapshot_date=date(2026, 8, 16),
            average_cost=None,
        )
        self.assertIsNone(position.average_cost)

    def test_known_cost_basis_requires_average_cost(self) -> None:
        with self.assertRaises(ValueError):
            HoldingsPosition(
                ticker='NHY.OL',
                quantity=10.0,
                position_status='ACTIVE',
                cost_basis_status=CostBasisStatus.KNOWN,
                snapshot_date=date(2026, 8, 16),
                average_cost=None,
            )

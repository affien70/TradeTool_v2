from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from tradetool.diagnostics.monthly_holdout_backtest import ROUND_TRIP_COST_RATE
from tradetool.diagnostics.monthly_holdout_review import (
    DECISION_BASELINE_COMPARISON,
    DECISION_UNDERPERFORMANCE,
    EXPECTED_REPORT_FILES,
    build_monthly_holdout_review,
    write_monthly_holdout_review_outputs,
)
from tradetool.diagnostics.monthly_holdout_review_cli import build_argument_parser
from tradetool.diagnostics.monthly_holdout_review_cli import main as monthly_holdout_review_cli_main
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import MAX_BUY_RAW_RANK, MIN_ACCEPTABLE_TRADED_VALUE, TRADE_POLICY_ENGINE_ID
from tradetool.ranking.baseline import BASELINE_RANKING_ENGINE_ID


def _pick(
    *,
    rebalance: str,
    rank: int,
    signal: str,
    candidate_type: str,
    excess20: float,
    excess60: float,
    complete20: bool = True,
    complete60: bool = True,
) -> dict[str, object]:
    return {
        'rebalance_date': rebalance,
        'effective_feature_date': rebalance,
        'ticker': f'T{rank:02d}.OL',
        'raw_rank': rank,
        'raw_score': float(100 - rank),
        'trade_signal': signal,
        'candidate_type': candidate_type,
        'entry_date': '2025-10-01',
        '20d_complete': complete20,
        '20d_return': excess20 + 0.02 if complete20 else None,
        '20d_net_return': excess20 + 0.02 - ROUND_TRIP_COST_RATE if complete20 else None,
        '20d_benchmark_return': 0.02 if complete20 else None,
        '20d_excess_return': excess20 if complete20 else None,
        '20d_net_excess_return': excess20 - ROUND_TRIP_COST_RATE if complete20 else None,
        '20d_missing_reason': '' if complete20 else 'missing_ticker_exit',
        '60d_complete': complete60,
        '60d_return': excess60 + 0.04 if complete60 else None,
        '60d_net_return': excess60 + 0.04 - ROUND_TRIP_COST_RATE if complete60 else None,
        '60d_benchmark_return': 0.04 if complete60 else None,
        '60d_excess_return': excess60 if complete60 else None,
        '60d_net_excess_return': excess60 - ROUND_TRIP_COST_RATE if complete60 else None,
        '60d_missing_reason': '' if complete60 else 'missing_ticker_exit',
    }


def _rows_for_month(rebalance: str, *, severe: bool = False, missing: bool = False) -> tuple[dict[str, object], ...]:
    if severe:
        top_excess = -0.12
        avoid_excess = 0.02
    else:
        top_excess = 0.02 if rebalance.endswith('31') else -0.01
        avoid_excess = -0.02
    rows: list[dict[str, object]] = []
    for rank in range(1, 11):
        rows.append(
            _pick(
                rebalance=rebalance,
                rank=rank,
                signal='BUY' if rank <= 4 else 'WATCH',
                candidate_type='Stable Leader' if rank <= 4 else 'Early Breakout',
                excess20=top_excess,
                excess60=top_excess,
                complete60=not missing,
            )
        )
    for rank in range(11, 16):
        rows.append(_pick(rebalance=rebalance, rank=rank, signal='REVIEW', candidate_type='Rebound Case', excess20=-0.01, excess60=-0.02))
    for rank in range(16, 26):
        rows.append(_pick(rebalance=rebalance, rank=rank, signal='AVOID', candidate_type='Reject', excess20=avoid_excess, excess60=avoid_excess))
    return tuple(rows)


def _monthly_result(*, months: tuple[str, ...] = ('2025-09-30', '2025-10-31', '2025-11-30', '2025-12-31', '2026-01-31', '2026-02-28'), severe: bool = False, missing: bool = False):
    pick_rows = tuple(row for month in months for row in _rows_for_month(month, severe=severe, missing=missing))
    missing_rows = tuple(
        {
            'rebalance_date': row['rebalance_date'],
            'ticker': row['ticker'],
            'raw_rank': row['raw_rank'],
            'trade_signal': row['trade_signal'],
            'candidate_type': row['candidate_type'],
            'forward_window_trading_days': 60,
            'missing_reason': 'missing_ticker_exit',
        }
        for row in pick_rows
        if row.get('60d_missing_reason')
    )
    return SimpleNamespace(
        universe_id='NORWAY_V2',
        universe_source='universe_cache:NORWAY_V2',
        benchmark_ticker='OSEBX.OL',
        rebalance_start_date=months[0],
        rebalance_end_date=months[-1],
        requested_rebalance_dates=months,
        data_source='yahoo',
        close_input_source='adjusted_close',
        forward_windows=(20, 60),
        transaction_cost_round_trip=ROUND_TRIP_COST_RATE,
        snapshot_count=len(months),
        valid_snapshot_count=len(months),
        total_ranked_count=len(pick_rows),
        monthly_rebalance_rows=tuple(
            {
                'rebalance_date': month,
                'effective_feature_date': month,
                'snapshot_valid': True,
                'ranked_count': 25,
                'feature_complete_count': 25,
                'feature_incomplete_count': 0,
                'missing_forward_exit_count': 25 if missing else 0,
                'single_snapshot_decision': 'continue_to_monthly_holdout_backtest',
                '20d_complete_count': 25,
                '60d_complete_count': 0 if missing else 25,
            }
            for month in months
        ),
        pick_rows=pick_rows,
        missing_exit_rows=missing_rows,
        decision_recommendation='continue_to_monthly_holdout_review',
    )


class MonthlyHoldoutReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.out_dir = Path('/tmp/tradetool_monthly_holdout_review_output')
        self.db_path = Path('/tmp/tradetool_monthly_holdout_review_readonly.sqlite')
        self._cleanup()
        self.db_path.write_text('readonly', encoding='utf-8')

    def tearDown(self) -> None:
        self._cleanup()

    def test_review_writes_expected_files(self) -> None:
        result = build_monthly_holdout_review(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2026, 2, 28),
            data_source='yahoo',
            monthly_builder=lambda **_: _monthly_result(),
        )
        write_monthly_holdout_review_outputs(result=result, out_dir=self.out_dir)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))
        summary = json.loads((self.out_dir / 'monthly_holdout_review_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['decision_recommendation'], DECISION_BASELINE_COMPARISON)

    def test_weak_alpha_does_not_trigger_tuning(self) -> None:
        result = build_monthly_holdout_review(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2026, 2, 28),
            data_source='yahoo',
            monthly_builder=lambda **_: _monthly_result(),
        )
        rendered = json.dumps(result.to_summary_dict())
        self.assertNotIn('tune', rendered.lower())
        self.assertEqual(result.decision_recommendation, DECISION_BASELINE_COMPARISON)

    def test_mixed_result_recommends_baseline_naive_comparison(self) -> None:
        result = build_monthly_holdout_review(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2026, 2, 28),
            data_source='yahoo',
            monthly_builder=lambda **_: _monthly_result(),
        )
        self.assertEqual(result.alpha_review['overall_performance_pattern'], 'mixed_or_unclear')
        self.assertEqual(result.decision_recommendation, DECISION_BASELINE_COMPARISON)

    def test_severe_underperformance_recommends_no_tuning_investigation(self) -> None:
        result = build_monthly_holdout_review(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2026, 2, 28),
            data_source='yahoo',
            monthly_builder=lambda **_: _monthly_result(severe=True),
        )
        self.assertEqual(result.decision_recommendation, DECISION_UNDERPERFORMANCE)
        self.assertFalse(result.red_flags['supports_immediate_tuning'])

    def test_top_vs_avoid_spread_and_hit_rate_are_calculated(self) -> None:
        result = build_monthly_holdout_review(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2026, 2, 28),
            data_source='yahoo',
            monthly_builder=lambda **_: _monthly_result(),
        )
        spread = next(row for row in result.top_bottom_spread_rows if row['comparison'] == 'raw_top_10_minus_AVOID' and row['forward_window_trading_days'] == 20)
        top10 = next(row for row in result.group_comparison_rows if row['group'] == 'raw_top_10' and row['forward_window_trading_days'] == 20)
        self.assertIn('mean_net_excess_spread', spread)
        self.assertAlmostEqual(float(spread['mean_net_excess_spread']), float(top10['mean_net_excess_return']) - -0.022)
        self.assertIn('hit_rate_vs_benchmark', top10)

    def test_missing_exits_are_surfaced_as_data_blocker(self) -> None:
        result = build_monthly_holdout_review(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2026, 2, 28),
            data_source='yahoo',
            monthly_builder=lambda **_: _monthly_result(missing=True),
        )
        self.assertGreater(result.technical_validity['missing_forward_exit_count'], 0)
        self.assertEqual(result.decision_recommendation, 'blocked_data_or_alignment_issue')

    def test_no_ranking_policy_feature_candidate_type_constants_changed(self) -> None:
        self.assertEqual(BASELINE_RANKING_ENGINE_ID, 'baseline_v0_price_volume_rs')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(MAX_BUY_RAW_RANK, 20)
        self.assertEqual(MIN_ACCEPTABLE_TRADED_VALUE, 750_000.0)

    def test_no_ml_or_holdings_output_fields(self) -> None:
        result = build_monthly_holdout_review(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2026, 2, 28),
            data_source='yahoo',
            monthly_builder=lambda **_: _monthly_result(),
        )
        for row in (*result.group_comparison_rows, *result.signal_quality_rows, *result.top_bottom_spread_rows, *result.period_breakdown_rows):
            lowered = {key.lower() for key in row}
            self.assertFalse(any('ml_score' in key for key in lowered))
            self.assertFalse(any('holdings' in key for key in lowered))
        summary = result.to_summary_dict()
        self.assertFalse(summary['leakage_controls']['ml_score_calculated'])
        self.assertFalse(summary['leakage_controls']['holdings_adjustment_applied'])

    def test_cli_requires_explicit_db_path_and_writes_outputs(self) -> None:
        parser = build_argument_parser()
        db_action = next(action for action in parser._actions if '--db-path' in action.option_strings)
        self.assertTrue(db_action.required)
        self.assertIsNone(db_action.default)

        import tradetool.diagnostics.monthly_holdout_review_cli as cli_module
        original = cli_module.build_monthly_holdout_review

        def builder(**kwargs):
            return build_monthly_holdout_review(**kwargs, monthly_builder=lambda **_: _monthly_result())

        try:
            cli_module.build_monthly_holdout_review = builder
            exit_code = monthly_holdout_review_cli_main([
                '--db-path', str(self.db_path),
                '--universe-id', 'NORWAY_V2',
                '--benchmark-ticker', 'OSEBX.OL',
                '--rebalance-start-date', '2025-09-30',
                '--rebalance-end-date', '2026-02-28',
                '--data-source', 'yahoo',
                '--out-dir', str(self.out_dir),
            ])
        finally:
            cli_module.build_monthly_holdout_review = original
        self.assertEqual(exit_code, 0)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))

    def _cleanup(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()
        if self.out_dir.exists():
            for child in self.out_dir.iterdir():
                child.unlink()
            self.out_dir.rmdir()


if __name__ == '__main__':
    unittest.main()

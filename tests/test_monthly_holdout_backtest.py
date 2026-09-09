from __future__ import annotations

import json
import os
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from tradetool.diagnostics.monthly_holdout_backtest import (
    DECISION_CONTINUE_REVIEW,
    DECISION_INVESTIGATE_UNDERPERFORMANCE,
    EXPECTED_REPORT_FILES,
    ROUND_TRIP_COST_RATE,
    build_monthly_holdout_backtest,
    generate_monthly_rebalance_dates,
    write_monthly_holdout_outputs,
)
from tradetool.diagnostics.monthly_holdout_backtest_cli import build_argument_parser
from tradetool.diagnostics.monthly_holdout_backtest_cli import main as monthly_holdout_cli_main
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import MAX_BUY_RAW_RANK, MIN_ACCEPTABLE_TRADED_VALUE, TRADE_POLICY_ENGINE_ID
from tradetool.ranking.baseline import BASELINE_RANKING_ENGINE_ID


def _candidate_row(
    *,
    rank: int,
    signal: str = 'BUY',
    candidate_type: str = 'Stable Leader',
    return_20: float = 0.08,
    excess_20: float = 0.03,
    return_60: float = 0.12,
    excess_60: float = 0.04,
    complete_20: bool = True,
    complete_60: bool = True,
) -> dict[str, object]:
    row = {
        'ticker': f'T{rank:02d}.OL',
        'raw_rank': rank,
        'raw_score': float(100 - rank),
        'trade_signal': signal,
        'candidate_type': candidate_type,
        'latest_feature_date': '2025-09-30',
        'entry_date': '2025-10-01',
        'entry_close': 100.0,
        '20d_complete': complete_20,
        '20d_exit_date': '2025-10-21' if complete_20 else None,
        '20d_return': return_20 if complete_20 else None,
        '20d_benchmark_entry_date': '2025-10-01' if complete_20 else None,
        '20d_benchmark_exit_date': '2025-10-21' if complete_20 else None,
        '20d_benchmark_return': return_20 - excess_20 if complete_20 else None,
        '20d_excess_return': excess_20 if complete_20 else None,
        '20d_missing_reason': '' if complete_20 else 'missing_ticker_exit',
        '60d_complete': complete_60,
        '60d_exit_date': '2025-11-30' if complete_60 else None,
        '60d_return': return_60 if complete_60 else None,
        '60d_benchmark_entry_date': '2025-10-01' if complete_60 else None,
        '60d_benchmark_exit_date': '2025-11-30' if complete_60 else None,
        '60d_benchmark_return': return_60 - excess_60 if complete_60 else None,
        '60d_excess_return': excess_60 if complete_60 else None,
        '60d_missing_reason': '' if complete_60 else 'missing_ticker_exit',
        'missing_reason': '' if complete_20 and complete_60 else 'missing_ticker_exit',
    }
    return row


def _snapshot_result(as_of: date, *, rows: tuple[dict[str, object], ...], decision: str = 'continue_to_monthly_holdout_backtest'):
    dated_rows: list[dict[str, object]] = []
    for source in rows:
        row = dict(source)
        row['latest_feature_date'] = as_of.isoformat()
        row['entry_date'] = (as_of + date.resolution).isoformat()
        row['20d_exit_date'] = (as_of + date.resolution * 21).isoformat() if row.get('20d_complete') else None
        row['20d_benchmark_entry_date'] = row['entry_date'] if row.get('20d_complete') else None
        row['20d_benchmark_exit_date'] = row['20d_exit_date'] if row.get('20d_complete') else None
        row['60d_exit_date'] = (as_of + date.resolution * 61).isoformat() if row.get('60d_complete') else None
        row['60d_benchmark_entry_date'] = row['entry_date'] if row.get('60d_complete') else None
        row['60d_benchmark_exit_date'] = row['60d_exit_date'] if row.get('60d_complete') else None
        dated_rows.append(row)
    missing_rows = []
    for row in dated_rows:
        for window in (20, 60):
            reason = row.get(f'{window}d_missing_reason')
            if reason:
                missing_rows.append(
                    {
                        'ticker': row['ticker'],
                        'raw_rank': row['raw_rank'],
                        'trade_signal': row['trade_signal'],
                        'candidate_type': row['candidate_type'],
                        'forward_window_trading_days': window,
                        'missing_reason': reason,
                    }
                )
    snapshot = SimpleNamespace(
        snapshot_valid=True,
        ranked_count=len(rows),
        feature_complete_count=len(rows),
        feature_incomplete_count=0,
    )
    return SimpleNamespace(
        universe_source='universe_cache:NORWAY_V2',
        benchmark_ticker='OSEBX.OL',
        as_of_date=as_of.isoformat(),
        effective_feature_date=as_of.isoformat(),
        data_source='yahoo',
        close_input_source='adjusted_close',
        forward_windows=(20, 60),
        snapshot=snapshot,
        candidate_rows=tuple(dated_rows),
        missing_exit_rows=tuple(missing_rows),
        decision_recommendation=decision,
    )


def _default_rows() -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for rank in range(1, 26):
        if rank <= 4:
            signal = 'BUY'
            candidate_type = 'Stable Leader'
        elif rank <= 8:
            signal = 'WATCH'
            candidate_type = 'Early Breakout'
        elif rank <= 13:
            signal = 'REVIEW'
            candidate_type = 'Rebound Case'
        else:
            signal = 'AVOID'
            candidate_type = 'Reject'
        rows.append(_candidate_row(rank=rank, signal=signal, candidate_type=candidate_type))
    return tuple(rows)


class MonthlyHoldoutBacktestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.out_dir = Path('/tmp/tradetool_monthly_holdout_output')
        self.db_path = Path('/tmp/tradetool_monthly_holdout_readonly.sqlite')
        self._cleanup()
        self.db_path.write_text('readonly', encoding='utf-8')

    def tearDown(self) -> None:
        self._cleanup()

    def test_monthly_rebalance_dates_are_generated_deterministically(self) -> None:
        dates = generate_monthly_rebalance_dates(date(2025, 7, 31), date(2026, 5, 29))
        self.assertEqual(dates[0], date(2025, 7, 31))
        self.assertEqual(dates[-1], date(2026, 5, 29))
        self.assertEqual(len(dates), 11)
        self.assertIn(date(2026, 2, 28), dates)

    def test_each_snapshot_uses_rebalance_date_as_as_of_cap(self) -> None:
        calls: list[date] = []

        def builder(**kwargs):
            calls.append(kwargs['as_of_date'])
            return _snapshot_result(kwargs['as_of_date'], rows=_default_rows())

        result = build_monthly_holdout_backtest(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2025, 11, 30),
            data_source='yahoo',
            forward_return_builder=builder,
        )
        self.assertEqual(calls, [date(2025, 9, 30), date(2025, 10, 31), date(2025, 11, 30)])
        self.assertTrue(all(row['entry_date'] > row['rebalance_date'] for row in result.pick_rows))

    def test_windows_and_benchmark_excess_aggregate_correctly(self) -> None:
        rows = (_candidate_row(rank=1, return_20=0.10, excess_20=0.03, return_60=0.20, excess_60=0.08),)

        def builder(**kwargs):
            return _snapshot_result(kwargs['as_of_date'], rows=rows)

        result = build_monthly_holdout_backtest(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2025, 11, 30),
            data_source='yahoo',
            forward_return_builder=builder,
        )
        top5_20 = next(row for row in result.topn_summary_rows if row['group'] == 'raw_top_5' and row['forward_window_trading_days'] == 20)
        top5_60 = next(row for row in result.topn_summary_rows if row['group'] == 'raw_top_5' and row['forward_window_trading_days'] == 60)
        self.assertAlmostEqual(top5_20['mean_forward_return'], 0.10)
        self.assertAlmostEqual(top5_20['mean_benchmark_return'], 0.07)
        self.assertAlmostEqual(top5_20['mean_excess_return'], 0.03)
        self.assertAlmostEqual(top5_60['mean_excess_return'], 0.08)

    def test_topn_and_signal_groups_are_calculated_correctly(self) -> None:
        def builder(**kwargs):
            return _snapshot_result(kwargs['as_of_date'], rows=_default_rows())

        result = build_monthly_holdout_backtest(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2025, 11, 30),
            data_source='yahoo',
            forward_return_builder=builder,
        )
        group_rows = {(row.get('rebalance_date'), row['group'], row['forward_window_trading_days']): row for row in result.monthly_group_summary_rows}
        self.assertEqual(group_rows[('2025-09-30', 'raw_top_5', 20)]['candidate_count'], 5)
        self.assertEqual(group_rows[('2025-09-30', 'raw_top_10', 20)]['candidate_count'], 10)
        self.assertEqual(group_rows[('2025-09-30', 'raw_top_20', 20)]['candidate_count'], 20)
        self.assertEqual(group_rows[('2025-09-30', 'BUY', 20)]['candidate_count'], 4)
        self.assertEqual(group_rows[('2025-09-30', 'BUY+WATCH', 20)]['candidate_count'], 8)
        self.assertEqual(group_rows[('2025-09-30', 'REVIEW', 20)]['candidate_count'], 5)
        self.assertEqual(group_rows[('2025-09-30', 'AVOID', 20)]['candidate_count'], 12)

    def test_candidate_type_grouping_is_calculated_correctly(self) -> None:
        def builder(**kwargs):
            return _snapshot_result(kwargs['as_of_date'], rows=_default_rows())

        result = build_monthly_holdout_backtest(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2025, 11, 30),
            data_source='yahoo',
            forward_return_builder=builder,
        )
        rows = {(row['group'], row['forward_window_trading_days']): row for row in result.candidate_type_summary_rows}
        self.assertEqual(rows[('Stable Leader', 20)]['candidate_count'], 12)
        self.assertEqual(rows[('Early Breakout', 20)]['candidate_count'], 12)
        self.assertEqual(rows[('Rebound Case', 20)]['candidate_count'], 15)
        self.assertEqual(rows[('Reject', 20)]['candidate_count'], 36)

    def test_net_return_applies_20_bps_round_trip_cost(self) -> None:
        rows = (_candidate_row(rank=1, return_20=0.10, excess_20=0.03),)

        def builder(**kwargs):
            return _snapshot_result(kwargs['as_of_date'], rows=rows)

        result = build_monthly_holdout_backtest(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2025, 11, 30),
            data_source='yahoo',
            forward_return_builder=builder,
        )
        self.assertEqual(result.transaction_cost_round_trip, ROUND_TRIP_COST_RATE)
        self.assertAlmostEqual(result.pick_rows[0]['20d_net_return'], 0.098)
        self.assertAlmostEqual(result.pick_rows[0]['20d_net_excess_return'], 0.028)

    def test_missing_exits_are_reported(self) -> None:
        rows = (_candidate_row(rank=1, complete_60=False),)

        def builder(**kwargs):
            return _snapshot_result(kwargs['as_of_date'], rows=rows)

        result = build_monthly_holdout_backtest(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2025, 11, 30),
            data_source='yahoo',
            forward_return_builder=builder,
        )
        self.assertEqual(len(result.missing_exit_rows), 3)
        self.assertTrue(all(row['missing_reason'] == 'missing_ticker_exit' for row in result.missing_exit_rows))

    def test_poor_performance_does_not_alter_ranking_or_policy(self) -> None:
        rows = tuple(
            _candidate_row(rank=rank, return_20=-0.20, excess_20=-0.20, return_60=-0.25, excess_60=-0.25)
            for rank in range(1, 11)
        )

        def builder(**kwargs):
            return _snapshot_result(kwargs['as_of_date'], rows=rows)

        result = build_monthly_holdout_backtest(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2025, 11, 30),
            data_source='yahoo',
            forward_return_builder=builder,
        )
        self.assertEqual(result.decision_recommendation, DECISION_INVESTIGATE_UNDERPERFORMANCE)
        self.assertEqual([row['raw_rank'] for row in result.pick_rows[:10]], list(range(1, 11)))
        self.assertTrue(all(row['trade_signal'] == 'BUY' for row in result.pick_rows[:10]))

    def test_output_files_are_written(self) -> None:
        def builder(**kwargs):
            return _snapshot_result(kwargs['as_of_date'], rows=_default_rows())

        result = build_monthly_holdout_backtest(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2025, 11, 30),
            data_source='yahoo',
            forward_return_builder=builder,
        )
        write_monthly_holdout_outputs(result=result, out_dir=self.out_dir)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))
        summary = json.loads((self.out_dir / 'monthly_holdout_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['decision_recommendation'], DECISION_CONTINUE_REVIEW)
        self.assertIn('current_candidate_list_used_as_target', summary['leakage_controls'])

    def test_no_ml_or_holdings_fields_are_output(self) -> None:
        def builder(**kwargs):
            return _snapshot_result(kwargs['as_of_date'], rows=_default_rows())

        result = build_monthly_holdout_backtest(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2025, 11, 30),
            data_source='yahoo',
            forward_return_builder=builder,
        )
        for row in result.pick_rows:
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

        import tradetool.diagnostics.monthly_holdout_backtest_cli as cli_module
        original = cli_module.build_monthly_holdout_backtest

        def builder(**kwargs):
            return build_monthly_holdout_backtest(
                **kwargs,
                forward_return_builder=lambda **inner_kwargs: _snapshot_result(inner_kwargs['as_of_date'], rows=_default_rows()),
            )

        try:
            cli_module.build_monthly_holdout_backtest = builder
            exit_code = monthly_holdout_cli_main([
                '--db-path', str(self.db_path),
                '--universe-id', 'NORWAY_V2',
                '--benchmark-ticker', 'OSEBX.OL',
                '--rebalance-start-date', '2025-09-30',
                '--rebalance-end-date', '2025-11-30',
                '--data-source', 'yahoo',
                '--out-dir', str(self.out_dir),
            ])
        finally:
            cli_module.build_monthly_holdout_backtest = original
        self.assertEqual(exit_code, 0)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))

    def test_no_db_writes_occur(self) -> None:
        before = self.db_path.stat().st_mtime_ns

        def builder(**kwargs):
            return _snapshot_result(kwargs['as_of_date'], rows=_default_rows())

        build_monthly_holdout_backtest(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2025, 11, 30),
            data_source='yahoo',
            forward_return_builder=builder,
        )
        self.assertEqual(before, self.db_path.stat().st_mtime_ns)

    def test_ranking_feature_policy_candidate_type_constants_are_unchanged(self) -> None:
        self.assertEqual(BASELINE_RANKING_ENGINE_ID, 'baseline_v0_price_volume_rs')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(MAX_BUY_RAW_RANK, 20)
        self.assertEqual(MIN_ACCEPTABLE_TRADED_VALUE, 750_000.0)

    def _cleanup(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()
        if self.out_dir.exists():
            for child in self.out_dir.iterdir():
                child.unlink()
            self.out_dir.rmdir()


if __name__ == '__main__':
    unittest.main()

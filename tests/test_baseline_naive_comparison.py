from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from tradetool.diagnostics.baseline_naive_comparison import (
    DECISION_CONTINUE_EXTENDED,
    DECISION_NAIVE_DOMINANCE,
    DECISION_V2_UNDERPERFORMANCE,
    EXPECTED_REPORT_FILES,
    build_baseline_naive_comparison,
    write_baseline_naive_comparison_outputs,
)
from tradetool.diagnostics.baseline_naive_comparison_cli import build_argument_parser
from tradetool.diagnostics.baseline_naive_comparison_cli import main as baseline_naive_cli_main
from tradetool.diagnostics.monthly_holdout_backtest import ROUND_TRIP_COST_RATE
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import MAX_BUY_RAW_RANK, MIN_ACCEPTABLE_TRADED_VALUE, TRADE_POLICY_ENGINE_ID
from tradetool.ranking.baseline import BASELINE_RANKING_ENGINE_ID


def _candidate(
    *,
    rank: int,
    signal: str,
    candidate_type: str,
    return_6m: float,
    rs_6m: float,
    rs_3m: float,
    excess20: float,
    excess60: float,
    above_sma200: bool = True,
) -> dict[str, object]:
    ticker = f'T{rank:02d}.OL'
    return {
        'ticker': ticker,
        'raw_rank': rank,
        'raw_score': float(100 - rank),
        'trade_signal': signal,
        'candidate_type': candidate_type,
        'latest_feature_date': '2025-09-30',
        'entry_date': '2025-10-01',
        '20d_complete': True,
        '20d_return': excess20 + 0.02,
        '20d_benchmark_return': 0.02,
        '20d_excess_return': excess20,
        '20d_missing_reason': '',
        '60d_complete': True,
        '60d_return': excess60 + 0.04,
        '60d_benchmark_return': 0.04,
        '60d_excess_return': excess60,
        '60d_missing_reason': '',
        '_features': {
            'ticker': ticker,
            'return_6m': return_6m,
            'relative_strength_6m': rs_6m,
            'relative_strength_3m': rs_3m,
            'above_sma200': above_sma200,
        },
    }


def _rows(*, v2_strong: bool = True, naive_dominates: bool = False) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for rank in range(1, 31):
        if rank <= 5:
            signal = 'BUY'
            candidate_type = 'Stable Leader'
        elif rank <= 10:
            signal = 'WATCH'
            candidate_type = 'Early Breakout'
        elif rank <= 15:
            signal = 'REVIEW'
            candidate_type = 'Rebound Case'
        else:
            signal = 'AVOID'
            candidate_type = 'Reject'
        is_naive_star = rank > 20
        v2_excess = 0.06 if v2_strong and rank <= 10 else -0.01
        if not v2_strong and 16 <= rank <= 20:
            v2_excess = 0.10
        if naive_dominates and is_naive_star:
            v2_excess = 0.20
        elif not v2_strong and is_naive_star:
            v2_excess = 0.03
        rows.append(
            _candidate(
                rank=rank,
                signal=signal,
                candidate_type=candidate_type,
                return_6m=100.0 - rank if is_naive_star else float(rank),
                rs_6m=200.0 - rank if is_naive_star else float(rank),
                rs_3m=300.0 - rank if is_naive_star else float(rank),
                excess20=v2_excess,
                excess60=v2_excess,
                above_sma200=rank % 2 == 0 or rank <= 10,
            )
        )
    return tuple(rows)


def _snapshot(as_of: date, *, rows: tuple[dict[str, object], ...], missing: bool = False):
    candidate_rows = []
    ranked_rows = []
    for source in rows:
        row = dict(source)
        features = dict(row.pop('_features'))
        if missing:
            row['60d_complete'] = False
            row['60d_return'] = None
            row['60d_benchmark_return'] = None
            row['60d_excess_return'] = None
            row['60d_missing_reason'] = 'missing_ticker_exit'
        candidate_rows.append(row)
        ranked_rows.append(features)
    missing_rows = tuple(
        {'ticker': row['ticker'], 'forward_window_trading_days': 60, 'missing_reason': 'missing_ticker_exit'}
        for row in candidate_rows
        if row.get('60d_missing_reason')
    )
    snapshot = SimpleNamespace(
        snapshot_valid=True,
        ranked_count=len(candidate_rows),
        ranked_rows=tuple(ranked_rows),
    )
    return SimpleNamespace(
        universe_source='universe_cache:NORWAY_V2',
        snapshot=snapshot,
        as_of_date=as_of.isoformat(),
        candidate_rows=tuple(candidate_rows),
        missing_exit_rows=missing_rows,
    )


class BaselineNaiveComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path('/tmp/tradetool_baseline_naive_readonly.sqlite')
        self.out_dir = Path('/tmp/tradetool_baseline_naive_output')
        self._cleanup()
        self.db_path.write_text('readonly', encoding='utf-8')

    def tearDown(self) -> None:
        self._cleanup()

    def _build(self, *, rows: tuple[dict[str, object]] | None = None, missing: bool = False):
        source_rows = _rows() if rows is None else rows

        def builder(**kwargs):
            return _snapshot(kwargs['as_of_date'], rows=source_rows, missing=missing)

        return build_baseline_naive_comparison(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2025, 11, 30),
            data_source='yahoo',
            forward_return_builder=builder,
        )

    def test_report_writes_expected_files(self) -> None:
        result = self._build()
        write_baseline_naive_comparison_outputs(result=result, out_dir=self.out_dir)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))
        summary = json.loads((self.out_dir / 'baseline_naive_comparison_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['decision_recommendation'], DECISION_CONTINUE_EXTENDED)

    def test_naive_momentum_groups_are_calculated_correctly(self) -> None:
        result = self._build()
        row = next(row for row in result.group_summary_rows if row['group'] == 'naive_return_6m_top_10' and row['forward_window_trading_days'] == 20)
        self.assertEqual(row['pick_count'], 30)
        self.assertEqual(row['rebalance_date_count'], 3)

    def test_naive_rs_groups_are_calculated_correctly(self) -> None:
        result = self._build()
        rs6 = next(row for row in result.group_summary_rows if row['group'] == 'naive_rs_6m_top_10' and row['forward_window_trading_days'] == 20)
        rs3 = next(row for row in result.group_summary_rows if row['group'] == 'naive_rs_3m_top_20' and row['forward_window_trading_days'] == 20)
        self.assertEqual(rs6['pick_count'], 30)
        self.assertEqual(rs3['pick_count'], 60)

    def test_equal_weight_universe_group_is_calculated_correctly(self) -> None:
        result = self._build()
        row = next(row for row in result.group_summary_rows if row['group'] == 'naive_equal_weight_feature_complete' and row['forward_window_trading_days'] == 20)
        self.assertEqual(row['pick_count'], 90)
        self.assertEqual(row['complete_count'], 90)

    def test_bottom_20_negative_control_group_is_calculated_correctly(self) -> None:
        result = self._build()
        bottom = next(row for row in result.group_summary_rows if row['group'] == 'v2_bottom_20_raw_rank' and row['forward_window_trading_days'] == 20)
        top20 = next(row for row in result.group_summary_rows if row['group'] == 'v2_raw_top_20' and row['forward_window_trading_days'] == 20)
        self.assertEqual(bottom['pick_count'], 60)
        self.assertEqual(top20['pick_count'], 60)

    def test_net_excess_applies_20_bps_round_trip_cost(self) -> None:
        result = self._build()
        top10 = next(row for row in result.group_summary_rows if row['group'] == 'v2_raw_top_10' and row['forward_window_trading_days'] == 20)
        self.assertAlmostEqual(top10['net_mean_excess_return'], top10['gross_mean_excess_return'] - ROUND_TRIP_COST_RATE)

    def test_comparison_uses_same_rebalance_dates_and_windows(self) -> None:
        result = self._build()
        self.assertEqual(result.requested_rebalance_dates, ('2025-09-30', '2025-10-31', '2025-11-30'))
        self.assertEqual(result.forward_windows, (20, 60))
        self.assertTrue(result.technical_validity['rebalance_dates_identical_for_all_groups'])
        self.assertTrue(result.technical_validity['forward_windows_identical_for_all_groups'])

    def test_decision_logic_handles_v2_wins(self) -> None:
        result = self._build()
        self.assertEqual(result.decision_recommendation, DECISION_CONTINUE_EXTENDED)
        self.assertTrue(result.quality_answers['v2_bottom20_performs_worse_than_top20'])

    def test_decision_logic_handles_v2_underperformance_and_naive_dominance(self) -> None:
        result = self._build(rows=_rows(v2_strong=False))
        self.assertEqual(result.decision_recommendation, DECISION_V2_UNDERPERFORMANCE)

        dominant = self._build(rows=_rows(v2_strong=False, naive_dominates=True))
        self.assertEqual(dominant.decision_recommendation, DECISION_NAIVE_DOMINANCE)

    def test_no_tuning_ranking_policy_constants_changed(self) -> None:
        self.assertEqual(BASELINE_RANKING_ENGINE_ID, 'baseline_v0_price_volume_rs')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(MAX_BUY_RAW_RANK, 20)
        self.assertEqual(MIN_ACCEPTABLE_TRADED_VALUE, 750_000.0)

    def test_no_ml_holdings_or_screener_output_fields(self) -> None:
        result = self._build()
        for row in (*result.group_summary_rows, *result.monthly_breakdown_rows, *result.topn_vs_naive_rows):
            lowered = {key.lower() for key in row}
            self.assertFalse(any('ml_score' in key for key in lowered))
            self.assertFalse(any('holdings' in key for key in lowered))
            self.assertFalse(any('screener' in key for key in lowered))
        summary = result.to_summary_dict()
        self.assertFalse(summary['leakage_controls']['tuning_applied'])
        self.assertFalse(summary['leakage_controls']['ml_score_calculated'])
        self.assertFalse(summary['leakage_controls']['holdings_adjustment_applied'])

    def test_missing_exits_block_comparison(self) -> None:
        result = self._build(missing=True)
        self.assertEqual(result.decision_recommendation, 'blocked_data_or_alignment_issue')

    def test_cli_requires_explicit_db_path_and_writes_outputs(self) -> None:
        parser = build_argument_parser()
        db_action = next(action for action in parser._actions if '--db-path' in action.option_strings)
        self.assertTrue(db_action.required)
        self.assertIsNone(db_action.default)

        import tradetool.diagnostics.baseline_naive_comparison_cli as cli_module
        original = cli_module.build_baseline_naive_comparison

        def builder(**kwargs):
            return build_baseline_naive_comparison(
                **kwargs,
                forward_return_builder=lambda **inner_kwargs: _snapshot(inner_kwargs['as_of_date'], rows=_rows()),
            )

        try:
            cli_module.build_baseline_naive_comparison = builder
            exit_code = baseline_naive_cli_main([
                '--db-path', str(self.db_path),
                '--universe-id', 'NORWAY_V2',
                '--benchmark-ticker', 'OSEBX.OL',
                '--rebalance-start-date', '2025-09-30',
                '--rebalance-end-date', '2025-11-30',
                '--data-source', 'yahoo',
                '--out-dir', str(self.out_dir),
            ])
        finally:
            cli_module.build_baseline_naive_comparison = original
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

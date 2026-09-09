from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from tradetool.diagnostics.naive_dominance_analysis import (
    DECISION_FIX_METHODOLOGY,
    DECISION_INVESTIGATE_POLICY,
    DECISION_PROMOTE_NAIVE_RS,
    EXPECTED_REPORT_FILES,
    build_naive_dominance_analysis,
    write_naive_dominance_analysis_outputs,
)
from tradetool.diagnostics.naive_dominance_analysis_cli import build_argument_parser
from tradetool.diagnostics.naive_dominance_analysis_cli import main as naive_dominance_cli_main
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import MAX_BUY_RAW_RANK, MIN_ACCEPTABLE_TRADED_VALUE, TRADE_POLICY_ENGINE_ID
from tradetool.ranking.baseline import BASELINE_RANKING_ENGINE_ID


def _candidate(
    *,
    rank: int,
    ticker: str | None = None,
    signal: str,
    candidate_type: str,
    return_6m: float,
    rs_6m: float,
    rs_3m: float,
    excess20: float,
    excess60: float,
) -> dict[str, object]:
    name = ticker or f'T{rank:02d}.OL'
    return {
        'ticker': name,
        'raw_rank': rank,
        'raw_score': float(1000 - rank),
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
            'ticker': name,
            'return_6m': return_6m,
            'relative_strength_6m': rs_6m,
            'relative_strength_3m': rs_3m,
            'above_sma200': True,
            'drawdown_252': -0.10 - rank / 1000,
            'distance_to_sma50': 0.02 + rank / 1000,
            'distance_to_sma200': 0.08 + rank / 1000,
            'volatility_63': 0.20 + rank / 1000,
        },
        'policy_reasons': 'unit policy reason',
        'policy_warnings': '',
        'classification_reasons': 'unit classification reason',
    }


def _rows(*, naive_dominates: bool = True, policy_filtering: bool = False) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for rank in range(1, 31):
        if rank <= 4:
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
        naive_star = rank > 20
        if naive_dominates:
            excess = 0.16 if naive_star else -0.03
        elif policy_filtering:
            excess = 0.12 if rank in {6, 7, 8, 9, 10} else -0.02
            if rank > 20:
                excess = 0.04
        else:
            excess = 0.06 if rank <= 10 else -0.01
        rows.append(
            _candidate(
                rank=rank,
                signal=signal,
                candidate_type=candidate_type,
                return_6m=300.0 - rank if naive_star else float(rank),
                rs_6m=400.0 - rank if naive_star else float(rank),
                rs_3m=500.0 - rank if naive_star else float(rank),
                excess20=excess,
                excess60=excess,
            )
        )
    return tuple(rows)


def _snapshot(as_of: date, *, rows: tuple[dict[str, object], ...], invalid: bool = False):
    candidate_rows = []
    ranked_rows = []
    for source in rows:
        row = dict(source)
        features = dict(row.pop('_features'))
        candidate_rows.append(row)
        ranked_rows.append(features)
    snapshot = SimpleNamespace(
        snapshot_valid=not invalid,
        ranked_count=len(candidate_rows),
        ranked_rows=tuple(ranked_rows),
    )
    return SimpleNamespace(
        universe_source='universe_cache:NORWAY_V2',
        benchmark_ticker='OSEBX.OL',
        snapshot=snapshot,
        as_of_date=as_of.isoformat(),
        candidate_rows=tuple(candidate_rows),
        missing_exit_rows=(),
    )


class NaiveDominanceAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path('/tmp/tradetool_naive_dominance_readonly.sqlite')
        self.out_dir = Path('/tmp/tradetool_naive_dominance_output')
        self._cleanup()
        self.db_path.write_text('readonly', encoding='utf-8')

    def tearDown(self) -> None:
        self._cleanup()

    def _build(self, *, rows: tuple[dict[str, object]] | None = None, invalid: bool = False):
        source_rows = _rows() if rows is None else rows

        def builder(**kwargs):
            return _snapshot(kwargs['as_of_date'], rows=source_rows, invalid=invalid)

        return build_naive_dominance_analysis(
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
        write_naive_dominance_analysis_outputs(result=result, out_dir=self.out_dir)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))
        summary = json.loads((self.out_dir / 'naive_dominance_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['decision_recommendation'], DECISION_PROMOTE_NAIVE_RS)

    def test_dominant_naive_group_identified(self) -> None:
        result = self._build()
        combined = next(row for row in result.dominant_group_rows if row['metric_scope'] == 'combined_average_20d_60d_net_excess')
        self.assertEqual(combined['dominant_group'], 'naive_rs_6m_top_10')
        self.assertEqual(result.dominant_naive_group, 'naive_rs_6m_top_10')

    def test_overlap_and_naive_only_winners_are_calculated(self) -> None:
        result = self._build()
        overlap = next(row for row in result.overlap_rows if row['v2_group'] == 'v2_raw_top_10' and row['forward_window_trading_days'] == 20)
        self.assertEqual(overlap['overlap_count'], 0)
        self.assertTrue(result.naive_only_winner_rows)
        self.assertTrue(all(row['v2_status'] == 'policy_filtered' for row in result.naive_only_winner_rows))

    def test_v2_only_losers_are_identified(self) -> None:
        result = self._build()
        self.assertTrue(result.v2_only_loser_rows)
        self.assertTrue(all(float(row['net_excess_return']) < 0.0 for row in result.v2_only_loser_rows))

    def test_policy_filtered_winners_are_identified(self) -> None:
        result = self._build()
        self.assertTrue(result.policy_filtered_winner_rows)
        self.assertTrue(all(row['trade_signal'] != 'BUY' for row in result.policy_filtered_winner_rows))

    def test_policy_filtering_problem_decision(self) -> None:
        result = self._build(rows=_rows(naive_dominates=False, policy_filtering=True))
        self.assertEqual(result.ranking_vs_policy_diagnosis, 'policy_filtering_suspect')
        self.assertEqual(result.decision_recommendation, DECISION_INVESTIGATE_POLICY)

    def test_aggregate_raw_top10_underperformance_promotes_naive_baseline_test(self) -> None:
        result = self._build()
        self.assertEqual(result.ranking_vs_policy_diagnosis, 'ranking_and_policy_stack_suspect')
        self.assertEqual(result.decision_recommendation, DECISION_PROMOTE_NAIVE_RS)

    def test_methodology_flaw_decision(self) -> None:
        result = self._build(invalid=True)
        self.assertEqual(result.decision_recommendation, DECISION_FIX_METHODOLOGY)

    def test_monthly_comparison_reports_breadth(self) -> None:
        result = self._build()
        self.assertTrue(result.monthly_rows)
        self.assertEqual(result.broad_vs_concentrated['naive_win_count'], 6)
        self.assertEqual(result.broad_vs_concentrated['v2_win_count'], 0)

    def test_feature_forward_relationship_output(self) -> None:
        result = self._build()
        rows = {(row['feature'], row['forward_window_trading_days']): row for row in result.feature_relationship_rows}
        self.assertIn(('relative_strength_6m', 20), rows)
        self.assertGreater(rows[('relative_strength_6m', 20)]['pair_count'], 0)

    def test_methodology_check_written(self) -> None:
        result = self._build()
        checks = {row['check_name']: row['passed'] for row in result.methodology_rows}
        self.assertTrue(checks['same_rebalance_dates'])
        self.assertTrue(checks['same_data_source'])
        self.assertTrue(checks['no_ml'])
        self.assertTrue(checks['no_holdings'])

    def test_no_tuning_ranking_policy_constants_changed(self) -> None:
        self.assertEqual(BASELINE_RANKING_ENGINE_ID, 'baseline_v0_price_volume_rs')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(MAX_BUY_RAW_RANK, 20)
        self.assertEqual(MIN_ACCEPTABLE_TRADED_VALUE, 750_000.0)

    def test_no_ml_holdings_or_screener_output_fields(self) -> None:
        result = self._build()
        for row in (
            *result.dominant_group_rows,
            *result.monthly_rows,
            *result.overlap_rows,
            *result.naive_only_winner_rows,
            *result.v2_only_loser_rows,
            *result.policy_filtered_winner_rows,
            *result.feature_relationship_rows,
        ):
            lowered = {key.lower() for key in row}
            self.assertFalse(any('ml_score' in key for key in lowered))
            self.assertFalse(any('holdings' in key for key in lowered))
            self.assertFalse(any('screener' in key for key in lowered))
        summary = result.to_summary_dict()
        self.assertFalse(summary['leakage_controls']['tuning_applied'])
        self.assertFalse(summary['leakage_controls']['ranking_formula_changed'])
        self.assertFalse(summary['leakage_controls']['trade_policy_thresholds_changed'])
        self.assertFalse(summary['leakage_controls']['candidate_type_rules_changed'])

    def test_cli_requires_explicit_db_path_and_writes_outputs(self) -> None:
        parser = build_argument_parser()
        db_action = next(action for action in parser._actions if '--db-path' in action.option_strings)
        self.assertTrue(db_action.required)
        self.assertIsNone(db_action.default)

        import tradetool.diagnostics.naive_dominance_analysis_cli as cli_module
        original = cli_module.build_naive_dominance_analysis

        def builder(**kwargs):
            return build_naive_dominance_analysis(
                **kwargs,
                forward_return_builder=lambda **inner_kwargs: _snapshot(inner_kwargs['as_of_date'], rows=_rows()),
            )

        try:
            cli_module.build_naive_dominance_analysis = builder
            exit_code = naive_dominance_cli_main([
                '--db-path', str(self.db_path),
                '--universe-id', 'NORWAY_V2',
                '--benchmark-ticker', 'OSEBX.OL',
                '--rebalance-start-date', '2025-09-30',
                '--rebalance-end-date', '2025-11-30',
                '--data-source', 'yahoo',
                '--out-dir', str(self.out_dir),
            ])
        finally:
            cli_module.build_naive_dominance_analysis = original
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

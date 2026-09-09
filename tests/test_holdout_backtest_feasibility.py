from __future__ import annotations

import csv
import json
import sqlite3
import unittest
from pathlib import Path

from tradetool.diagnostics.holdout_backtest_feasibility import (
    DECISION_COMPARE_BASELINE,
    DECISION_IMPLEMENT_SNAPSHOT,
    EXPECTED_REPORT_FILES,
    build_engine_gap_assessment,
    build_evidence_inventory,
    build_holdout_backtest_feasibility,
    build_leakage_risk_register,
    build_proposed_protocol,
    recommend_next_action,
    write_holdout_backtest_feasibility_outputs,
)
from tradetool.diagnostics.holdout_backtest_feasibility_cli import build_argument_parser
from tradetool.diagnostics.holdout_backtest_feasibility_cli import main as holdout_backtest_feasibility_cli_main
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import MIN_ACCEPTABLE_TRADED_VALUE, TRADE_POLICY_ENGINE_ID
from tradetool.ranking.baseline import BASELINE_RANKING_ENGINE_ID


class HoldoutBacktestFeasibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path('/tmp/tradetool_holdout_feasibility_unit_root')
        self.out_dir = Path('/tmp/tradetool_holdout_feasibility_unit_output')
        self._remove_tree(self.root)
        self._remove_tree(self.out_dir)
        (self.root / 'src' / 'tradetool' / 'diagnostics').mkdir(parents=True)
        (self.root / 'evidence' / 'v1_baseline' / '20260622T200335Z').mkdir(parents=True)

    def tearDown(self) -> None:
        self._remove_tree(self.root)
        self._remove_tree(self.out_dir)
        db_path = Path('/tmp/tradetool_holdout_feasibility_nowrite.sqlite')
        if db_path.exists():
            db_path.unlink()

    def test_report_writes_all_expected_files(self) -> None:
        self._write_phase1_evidence(methods=('main_momentum', 'raw_ml', 'parked_practical_first'))
        result = build_holdout_backtest_feasibility(
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            project_root=self.root,
        )
        write_holdout_backtest_feasibility_outputs(result=result, out_dir=self.out_dir)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))
        summary = json.loads((self.out_dir / 'holdout_backtest_feasibility_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['decision_recommendation'], DECISION_IMPLEMENT_SNAPSHOT)

    def test_protocol_includes_20d_60d_benchmark_excess_and_costs(self) -> None:
        protocol = build_proposed_protocol(universe_id='NORWAY_V2', benchmark_ticker='OSEBX.OL')
        self.assertIn(20, {row['forward_window_trading_days'] for row in protocol})
        self.assertIn(60, {row['forward_window_trading_days'] for row in protocol})
        self.assertTrue(any('excess_forward_return' in row['gross_return_metrics'] for row in protocol))
        self.assertTrue(any('10 bps one-way' in row['cost_assumption'] for row in protocol))
        self.assertTrue(any(row['selection_bucket'] == 'buy_watch' for row in protocol))

    def test_leakage_controls_are_explicit(self) -> None:
        controls = build_leakage_risk_register()
        risk_ids = {row['risk_id'] for row in controls}
        self.assertIn('point_in_time_features', risk_ids)
        self.assertIn('forward_label_start', risk_ids)
        self.assertIn('survivorship_membership', risk_ids)
        self.assertIn('no_holdings_adjustment', risk_ids)

    def test_missing_evidence_is_reported_honestly(self) -> None:
        rows = build_evidence_inventory(project_root=self.root)
        by_id = {row['evidence_id']: row for row in rows}
        self.assertFalse(by_id['phase1_ose_baseline_top20']['available'])
        self.assertFalse(by_id['phase1_focus_ticker_baseline']['available'])

    def test_engine_gaps_classify_missing_primitives(self) -> None:
        gaps = build_engine_gap_assessment(project_root=self.root)
        by_capability = {row['capability']: row['status'] for row in gaps}
        self.assertEqual(by_capability['compute_feature_rows_as_of_date'], 'missing')
        self.assertEqual(by_capability['calculate_forward_returns'], 'missing')
        self.assertEqual(by_capability['aggregate_monthly_backtest_results'], 'missing')
        self.assertEqual(by_capability['run_ranking_as_of_date'], 'ready')

    def test_decision_returns_implement_snapshot_when_feasible(self) -> None:
        evidence = ({'evidence_id': 'phase1_ose_baseline_top20', 'available': True},)
        gaps = (
            {'capability': 'compute_feature_rows_as_of_date', 'status': 'missing'},
            {'capability': 'calculate_forward_returns', 'status': 'missing'},
            {'capability': 'aggregate_monthly_backtest_results', 'status': 'missing'},
            {'capability': 'run_ranking_as_of_date', 'status': 'ready'},
        )
        decision, reasons = recommend_next_action(evidence_rows=evidence, engine_gap_rows=gaps)
        self.assertEqual(decision, DECISION_IMPLEMENT_SNAPSHOT)
        self.assertIn('historical_as_of_and_forward_return_primitives_missing_but_feasible', reasons)

    def test_decision_reports_missing_phase1_baseline_before_backtest(self) -> None:
        decision, reasons = recommend_next_action(evidence_rows=(), engine_gap_rows=())
        self.assertEqual(decision, DECISION_COMPARE_BASELINE)
        self.assertIn('phase1_ose_baseline_top20_missing', reasons)

    def test_cli_has_no_db_path_argument_or_production_db_default(self) -> None:
        parser = build_argument_parser()
        options = {option for action in parser._actions for option in action.option_strings}
        self.assertNotIn('--db-path', options)
        self.assertNotIn('--database-path', options)

    def test_no_db_writes_occur(self) -> None:
        db_path = Path('/tmp/tradetool_holdout_feasibility_nowrite.sqlite')
        with sqlite3.connect(db_path) as connection:
            connection.execute('CREATE TABLE marker (id INTEGER)')
            connection.execute('INSERT INTO marker VALUES (1)')
            connection.commit()
        before = db_path.stat().st_mtime_ns
        build_holdout_backtest_feasibility(
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            project_root=self.root,
        )
        self.assertEqual(before, db_path.stat().st_mtime_ns)

    def test_cli_writes_expected_files(self) -> None:
        self._write_phase1_evidence(methods=('main_momentum', 'raw_ml', 'parked_practical_first'))
        exit_code = holdout_backtest_feasibility_cli_main([
            '--universe-id', 'NORWAY_V2',
            '--benchmark-ticker', 'OSEBX.OL',
            '--project-root', str(self.root),
            '--out-dir', str(self.out_dir),
        ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))

    def test_no_ranking_feature_policy_candidate_type_ml_or_holdings_logic_changes(self) -> None:
        self.assertEqual(BASELINE_RANKING_ENGINE_ID, 'baseline_v0_price_volume_rs')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(MIN_ACCEPTABLE_TRADED_VALUE, 750_000.0)
        source = Path('src/tradetool/diagnostics/holdout_backtest_feasibility.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('ml_runs', source)
        self.assertNotIn('from tradetool.holdings', source)
        self.assertNotIn('write_market_data_rows', source)
        self.assertNotIn('build_baseline_ranking(', source)

    def _write_phase1_evidence(self, *, methods: tuple[str, ...]) -> None:
        path = self.root / 'evidence' / 'v1_baseline' / '20260622T200335Z' / 'ose_method_comparison_top20.csv'
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['method', 'ticker', 'method_rank'])
            for index, method in enumerate(methods, start=1):
                writer.writerow([method, f'T{index}.OL', index])
        for name in (
            'artifact_inventory.json',
            'universe_coverage.json',
            'limitations.md',
        ):
            (path.parent / name).write_text('{}', encoding='utf-8')
        for name in (
            'candidate_quality_comparison.py',
            'market_data_v2_readiness.py',
            'market_data_write_test.py',
        ):
            (self.root / 'src' / 'tradetool' / 'diagnostics' / name).write_text('', encoding='utf-8')

    @staticmethod
    def _remove_tree(path: Path) -> None:
        if not path.exists():
            return
        for child in sorted(path.rglob('*'), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        path.rmdir()


if __name__ == '__main__':
    unittest.main()

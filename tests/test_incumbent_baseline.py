from __future__ import annotations

import json
import unittest
from pathlib import Path

from tradetool.diagnostics.incumbent_baseline import (
    BASELINE_ID,
    BENCHMARK_TICKER,
    CLOSE_SOURCE,
    DECISION_NEXT_ACTION,
    EXPECTED_REPORT_FILES,
    SELECTION_LIMIT,
    build_incumbent_baseline_definition,
    select_incumbent_baseline,
    write_incumbent_baseline_outputs,
)
from tradetool.diagnostics.incumbent_baseline_cli import build_argument_parser
from tradetool.diagnostics.incumbent_baseline_cli import main as incumbent_baseline_cli_main
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import MAX_BUY_RAW_RANK, MIN_ACCEPTABLE_TRADED_VALUE, TRADE_POLICY_ENGINE_ID
from tradetool.ranking.baseline import BASELINE_RANKING_ENGINE_ID


class IncumbentBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.out_dir = Path('/tmp/tradetool_incumbent_baseline_output')
        self._cleanup()

    def tearDown(self) -> None:
        self._cleanup()

    def test_definition_contains_approved_baseline_id_and_rule(self) -> None:
        result = build_incumbent_baseline_definition()
        self.assertEqual(result.baseline_id, BASELINE_ID)
        self.assertEqual(result.baseline_id, 'incumbent_naive_rs_6m_top_10_v0')
        self.assertEqual(result.benchmark_ticker, BENCHMARK_TICKER)
        self.assertEqual(result.benchmark_ticker, 'OSEBX.OL')
        self.assertEqual(result.close_source, CLOSE_SOURCE)
        self.assertIn('relative_strength_6m descending', result.selection_rule)
        self.assertIn('ticker ascending', result.selection_rule)
        self.assertEqual(result.decision_recommendation, DECISION_NEXT_ACTION)

    def test_selection_uses_feature_complete_rows_by_6m_rs_then_ticker(self) -> None:
        rows = (
            {'ticker': 'CCC.OL', 'relative_strength_6m': 0.4},
            {'ticker': 'AAA.OL', 'relative_strength_6m': 0.5},
            {'ticker': 'BBB.OL', 'relative_strength_6m': 0.5},
            {'ticker': 'DDD.OL', 'relative_strength_6m': None},
        )
        selected = select_incumbent_baseline(rows, limit=3)
        self.assertEqual([row['ticker'] for row in selected], ['AAA.OL', 'BBB.OL', 'CCC.OL'])

    def test_selection_limit_is_top_10(self) -> None:
        rows = tuple({'ticker': f'T{index:02d}.OL', 'relative_strength_6m': float(index)} for index in range(12))
        selected = select_incumbent_baseline(rows)
        self.assertEqual(len(selected), SELECTION_LIMIT)
        self.assertEqual(selected[0]['ticker'], 'T11.OL')
        self.assertEqual(selected[-1]['ticker'], 'T02.OL')

    def test_outputs_are_written_with_expected_schema(self) -> None:
        result = build_incumbent_baseline_definition()
        write_incumbent_baseline_outputs(result=result, out_dir=self.out_dir)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))
        summary = json.loads((self.out_dir / 'incumbent_baseline_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['baseline_id'], BASELINE_ID)
        self.assertEqual(summary['decision_recommendation'], DECISION_NEXT_ACTION)
        definition = (self.out_dir / 'incumbent_baseline_definition.csv').read_text(encoding='utf-8')
        self.assertIn('relative_strength_6m', definition)
        self.assertIn('ticker ascending', definition)

    def test_cli_requires_output_directory_and_writes_outputs(self) -> None:
        parser = build_argument_parser()
        out_action = next(action for action in parser._actions if '--out-dir' in action.option_strings)
        self.assertTrue(out_action.required)
        exit_code = incumbent_baseline_cli_main(['--out-dir', str(self.out_dir)])
        self.assertEqual(exit_code, 0)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))

    def test_no_tuning_ranking_policy_ml_or_holdings_changes(self) -> None:
        self.assertEqual(BASELINE_RANKING_ENGINE_ID, 'baseline_v0_price_volume_rs')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(MAX_BUY_RAW_RANK, 20)
        self.assertEqual(MIN_ACCEPTABLE_TRADED_VALUE, 750_000.0)
        source = Path('src/tradetool/diagnostics/incumbent_baseline.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('streamlit', source)
        self.assertNotIn('ml_runs', source)
        self.assertNotIn('tradetool.holdings', source)
        self.assertNotIn('write_market_data', source)

    def _cleanup(self) -> None:
        if not self.out_dir.exists():
            return
        for child in self.out_dir.iterdir():
            child.unlink()
        self.out_dir.rmdir()


if __name__ == '__main__':
    unittest.main()

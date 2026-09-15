from __future__ import annotations

import json
import unittest
from pathlib import Path

from tradetool.diagnostics.challenger_no_drawdown_comparison import CHALLENGER_ID
from tradetool.diagnostics.cross_universe_decision import (
    DECISION_RECOMMENDATION,
    EXPECTED_REPORT_FILES,
    NEXT_RECOMMENDED_ACTION,
    build_cross_universe_decision,
    write_cross_universe_decision_outputs,
)
from tradetool.diagnostics.cross_universe_decision_cli import build_argument_parser
from tradetool.diagnostics.cross_universe_decision_cli import main as cross_universe_cli_main
from tradetool.diagnostics.incumbent_baseline import BASELINE_ID
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import MAX_BUY_RAW_RANK, TRADE_POLICY_ENGINE_ID
from tradetool.ranking.baseline import BASELINE_RANKING_ENGINE_ID


class CrossUniverseDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.out_dir = Path('/tmp/tradetool_cross_universe_decision_output')
        self._cleanup()

    def tearDown(self) -> None:
        self._cleanup()

    def test_embedded_result_matrix_contains_norway_and_sp500_evidence(self) -> None:
        result = build_cross_universe_decision()
        rows = {row.universe_id: row for row in result.result_rows}
        self.assertEqual(set(rows), {'NORWAY_V2', 'SP500'})
        self.assertEqual(rows['NORWAY_V2'].benchmark_ticker, 'OSEBX.OL')
        self.assertEqual(rows['SP500'].benchmark_ticker, '^GSPC')
        self.assertEqual(rows['NORWAY_V2'].rebalance_date_count, 41)
        self.assertEqual(rows['SP500'].rebalance_date_count, 41)
        self.assertEqual(rows['NORWAY_V2'].ranked_total, 10516)
        self.assertEqual(rows['SP500'].ranked_total, 20199)
        self.assertAlmostEqual(rows['NORWAY_V2'].spread_60d_net_excess, 0.012012)
        self.assertAlmostEqual(rows['SP500'].spread_60d_net_excess, -0.004179)
        self.assertAlmostEqual(rows['SP500'].v2_raw_top10_60d_net_excess or 0.0, -0.018679)

    def test_decision_and_next_action_are_locked(self) -> None:
        result = build_cross_universe_decision()
        self.assertEqual(result.incumbent_id, BASELINE_ID)
        self.assertEqual(result.challenger_id, CHALLENGER_ID)
        self.assertEqual(result.decision_recommendation, DECISION_RECOMMENDATION)
        self.assertEqual(result.next_recommended_action, NEXT_RECOMMENDED_ACTION)

    def test_outputs_are_written_with_expected_schema(self) -> None:
        result = build_cross_universe_decision()
        write_cross_universe_decision_outputs(result=result, out_dir=self.out_dir)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))
        summary = json.loads((self.out_dir / 'cross_universe_decision_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['decision_recommendation'], DECISION_RECOMMENDATION)
        self.assertEqual(summary['next_recommended_action'], NEXT_RECOMMENDED_ACTION)
        self.assertEqual(summary['universes_tested'], ['NORWAY_V2', 'SP500'])
        matrix = (self.out_dir / 'cross_universe_result_matrix.csv').read_text(encoding='utf-8')
        self.assertIn('incumbent_60d_net_excess', matrix)
        self.assertIn('v2_raw_top10_60d_net_excess', matrix)
        decision = (self.out_dir / 'cross_universe_decision.csv').read_text(encoding='utf-8')
        self.assertIn(DECISION_RECOMMENDATION, decision)

    def test_cli_requires_out_dir_and_writes_outputs(self) -> None:
        parser = build_argument_parser()
        out_action = next(action for action in parser._actions if '--out-dir' in action.option_strings)
        self.assertTrue(out_action.required)
        self.assertIsNone(out_action.default)
        exit_code = cross_universe_cli_main(['--out-dir', str(self.out_dir)])
        self.assertEqual(exit_code, 0)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))

    def test_no_ranking_policy_ui_ml_or_holdings_changes(self) -> None:
        self.assertEqual(BASELINE_RANKING_ENGINE_ID, 'baseline_v0_price_volume_rs')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(MAX_BUY_RAW_RANK, 20)
        source = Path('src/tradetool/diagnostics/cross_universe_decision.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('streamlit', source)
        self.assertNotIn('ml_runs', source)
        self.assertNotIn('tradetool.holdings', source)
        self.assertNotIn('write_market_data', source)

    def _cleanup(self) -> None:
        if self.out_dir.exists():
            for child in self.out_dir.iterdir():
                child.unlink()
            self.out_dir.rmdir()


if __name__ == '__main__':
    unittest.main()

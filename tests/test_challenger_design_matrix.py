from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from tradetool.diagnostics.challenger_design_matrix import (
    CHALLENGER_IDS,
    CHALLENGER_RS6M_3M_TIEBREAK,
    CHALLENGER_RS6M_LIQUIDITY,
    CHALLENGER_RS6M_POSITIVE_3M,
    CHALLENGER_RS6M_TREND,
    DECISION_CANDIDATE_FOUND,
    DECISION_FIX_METHODOLOGY,
    DECISION_KEEP_INCUMBENT,
    EXPECTED_REPORT_FILES,
    build_challenger_design_matrix,
    select_design_challenger,
    write_challenger_design_matrix_outputs,
)
from tradetool.diagnostics.challenger_design_matrix_cli import build_argument_parser
from tradetool.diagnostics.challenger_design_matrix_cli import main as design_matrix_cli_main
from tradetool.diagnostics.incumbent_baseline import BASELINE_ID
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import MAX_BUY_RAW_RANK, TRADE_POLICY_ENGINE_ID
from tradetool.ranking.baseline import BASELINE_RANKING_ENGINE_ID


def _candidate(
    *,
    rank: int,
    rs6: float,
    rs3: float,
    return6: float,
    excess20: float,
    excess60: float,
    above_sma200: bool = True,
    traded_value: float = 1_000_000.0,
) -> dict[str, object]:
    ticker = f'T{rank:02d}.OL'
    return {
        'ticker': ticker,
        'raw_rank': rank,
        'raw_score': float(100 - rank),
        'trade_signal': 'BUY',
        'candidate_type': 'Stable Leader',
        'latest_feature_date': '2025-09-30',
        'entry_date': '2025-10-01',
        '20d_complete': True,
        '20d_return': excess20 + 0.02,
        '20d_benchmark_return': 0.02,
        '20d_excess_return': excess20,
        '60d_complete': True,
        '60d_return': excess60 + 0.04,
        '60d_benchmark_return': 0.04,
        '60d_excess_return': excess60,
        '_features': {
            'ticker': ticker,
            'return_6m': return6,
            'relative_strength_6m': rs6,
            'relative_strength_3m': rs3,
            'above_sma200': above_sma200,
            'average_traded_value_20': traded_value,
        },
    }


def _rows(*, challenger_wins: bool = True, no_candidate: bool = False) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for rank in range(1, 31):
        incumbent_member = rank <= 10
        challenger_member = 11 <= rank <= 20
        excess20 = 0.03
        excess60 = 0.06
        if challenger_wins and challenger_member:
            excess20 = 0.04
            excess60 = 0.16
        if no_candidate and incumbent_member:
            excess20 = 0.08
            excess60 = 0.20
        rows.append(
            _candidate(
                rank=rank,
                rs6=300.0 - rank if incumbent_member else 200.0 - rank,
                rs3=500.0 - rank if challenger_member else (-1.0 if rank <= 10 else 1.0),
                return6=0.5 if rank <= 20 else -0.1,
                above_sma200=rank <= 20,
                traded_value=1_000_000.0 if rank != 12 else 100.0,
                excess20=excess20,
                excess60=excess60,
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
        universe_source='universe_cache:TEST',
        snapshot=snapshot,
        as_of_date=as_of.isoformat(),
        candidate_rows=tuple(candidate_rows),
        missing_exit_rows=(),
    )


class ChallengerDesignMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path('/tmp/tradetool_challenger_design_matrix.sqlite')
        self.out_dir = Path('/tmp/tradetool_challenger_design_matrix_output')
        self._cleanup()
        self.db_path.write_text('readonly', encoding='utf-8')

    def tearDown(self) -> None:
        self._cleanup()

    def _build(self, *, rows: tuple[dict[str, object]] | None = None, invalid: bool = False):
        source_rows = _rows() if rows is None else rows

        def builder(**kwargs):
            return _snapshot(kwargs['as_of_date'], rows=source_rows, invalid=invalid)

        return build_challenger_design_matrix(
            db_path=self.db_path,
            universe_id='TEST',
            benchmark_ticker='BENCH',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2025, 11, 30),
            data_source='yahoo',
            forward_return_builder=builder,
        )

    def test_selectors_apply_expected_rules_without_drawdown_gate(self) -> None:
        rows = (
            {'ticker': 'BBB', 'relative_strength_6m': 1.0, 'relative_strength_3m': 0.5, 'average_traded_value_20': 1_000_000.0, 'above_sma200': True, 'return_6m': 0.2},
            {'ticker': 'AAA', 'relative_strength_6m': 1.0, 'relative_strength_3m': 0.6, 'average_traded_value_20': 1_000_000.0, 'above_sma200': True, 'return_6m': 0.2},
            {'ticker': 'NEG3M', 'relative_strength_6m': 2.0, 'relative_strength_3m': -0.1, 'average_traded_value_20': 1_000_000.0, 'above_sma200': True, 'return_6m': 0.2},
            {'ticker': 'ILLIQ', 'relative_strength_6m': 3.0, 'relative_strength_3m': 0.1, 'average_traded_value_20': 10.0, 'above_sma200': True, 'return_6m': 0.2},
            {'ticker': 'DOWN', 'relative_strength_6m': 4.0, 'relative_strength_3m': 0.1, 'average_traded_value_20': 1_000_000.0, 'above_sma200': False, 'return_6m': -0.2},
        )
        self.assertEqual([row['ticker'] for row in select_design_challenger(rows, CHALLENGER_RS6M_3M_TIEBREAK, limit=3)], ['DOWN', 'ILLIQ', 'NEG3M'])
        self.assertNotIn('NEG3M', [row['ticker'] for row in select_design_challenger(rows, CHALLENGER_RS6M_POSITIVE_3M)])
        self.assertNotIn('ILLIQ', [row['ticker'] for row in select_design_challenger(rows, CHALLENGER_RS6M_LIQUIDITY)])
        self.assertNotIn('DOWN', [row['ticker'] for row in select_design_challenger(rows, CHALLENGER_RS6M_TREND)])

    def test_report_writes_expected_files_and_candidate_found_decision(self) -> None:
        result = self._build()
        write_challenger_design_matrix_outputs(result=result, out_dir=self.out_dir)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))
        summary = json.loads((self.out_dir / 'challenger_design_matrix_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['incumbent_id'], BASELINE_ID)
        self.assertEqual(summary['challenger_ids'], list(CHALLENGER_IDS))
        self.assertEqual(summary['decision_recommendation'], DECISION_CANDIDATE_FOUND)

    def test_group_summary_monthly_and_overlap_include_all_challengers(self) -> None:
        result = self._build()
        groups = {row['group'] for row in result.group_summary_rows}
        self.assertIn(BASELINE_ID, groups)
        self.assertTrue(set(CHALLENGER_IDS).issubset(groups))
        overlap_groups = {row['challenger_id'] for row in result.overlap_rows}
        self.assertEqual(overlap_groups, set(CHALLENGER_IDS))

    def test_keep_incumbent_decision_when_no_challenger_beats(self) -> None:
        result = self._build(rows=_rows(challenger_wins=False, no_candidate=True))
        self.assertEqual(result.decision_recommendation, DECISION_KEEP_INCUMBENT)
        self.assertIsNone(result.best_challenger_id)

    def test_fix_methodology_decision_on_invalid_snapshots(self) -> None:
        result = self._build(invalid=True)
        self.assertEqual(result.decision_recommendation, DECISION_FIX_METHODOLOGY)

    def test_cli_requires_explicit_db_path_and_writes_outputs(self) -> None:
        parser = build_argument_parser()
        db_action = next(action for action in parser._actions if '--db-path' in action.option_strings)
        self.assertTrue(db_action.required)
        self.assertIsNone(db_action.default)

        import tradetool.diagnostics.challenger_design_matrix_cli as cli_module
        original = cli_module.build_challenger_design_matrix

        def builder(**kwargs):
            return build_challenger_design_matrix(
                **kwargs,
                forward_return_builder=lambda **inner_kwargs: _snapshot(inner_kwargs['as_of_date'], rows=_rows()),
            )

        try:
            cli_module.build_challenger_design_matrix = builder
            exit_code = design_matrix_cli_main([
                '--db-path', str(self.db_path),
                '--universe-id', 'TEST',
                '--benchmark-ticker', 'BENCH',
                '--rebalance-start-date', '2025-09-30',
                '--rebalance-end-date', '2025-11-30',
                '--data-source', 'yahoo',
                '--out-dir', str(self.out_dir),
            ])
        finally:
            cli_module.build_challenger_design_matrix = original
        self.assertEqual(exit_code, 0)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))

    def test_no_production_ui_policy_ml_or_holdings_changes(self) -> None:
        self.assertEqual(BASELINE_RANKING_ENGINE_ID, 'baseline_v0_price_volume_rs')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(MAX_BUY_RAW_RANK, 20)
        source = Path('src/tradetool/diagnostics/challenger_design_matrix.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('streamlit', source)
        self.assertNotIn('ml_runs', source)
        self.assertNotIn('tradetool.holdings', source)
        self.assertNotIn('write_market_data', source)

    def _cleanup(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()
        if self.out_dir.exists():
            for child in self.out_dir.iterdir():
                child.unlink()
            self.out_dir.rmdir()


if __name__ == '__main__':
    unittest.main()

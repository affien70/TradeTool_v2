from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from tradetool.diagnostics.challenger_comparison import (
    CHALLENGER_ID,
    DECISION_FILTER_DAMAGE,
    DECISION_KEEP_INCUMBENT,
    DECISION_PROMOTE,
    EXPECTED_REPORT_FILES,
    GROUP_CHALLENGER,
    GROUP_INCUMBENT,
    build_challenger_comparison,
    select_challenger_rs6m_trend_risk,
    write_challenger_comparison_outputs,
)
from tradetool.diagnostics.challenger_comparison_cli import build_argument_parser
from tradetool.diagnostics.challenger_comparison_cli import main as challenger_cli_main
from tradetool.diagnostics.incumbent_baseline import BASELINE_ID
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import MAX_BUY_RAW_RANK, MIN_ACCEPTABLE_TRADED_VALUE, TRADE_POLICY_ENGINE_ID
from tradetool.ranking.baseline import BASELINE_RANKING_ENGINE_ID


def _candidate(
    *,
    rank: int,
    signal: str = 'BUY',
    candidate_type: str = 'Stable Leader',
    rs6: float,
    rs3: float,
    return6: float,
    excess20: float,
    excess60: float,
    above_sma200: bool = True,
    traded_value: float = 1_000_000.0,
    drawdown: float = -0.10,
    stretch: float = 0.10,
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
            'return_6m': return6,
            'relative_strength_6m': rs6,
            'relative_strength_3m': rs3,
            'above_sma200': above_sma200,
            'average_traded_value_20': traded_value,
            'drawdown_252': drawdown,
            'distance_to_sma200': stretch,
        },
    }


def _rows(*, challenger_wins: bool = True, filter_damage: bool = False) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for rank in range(1, 31):
        incumbent_member = rank <= 10
        challenger_member = 11 <= rank <= 20
        if challenger_wins:
            excess20 = 0.04 if challenger_member else 0.02
            excess60 = 0.12 if challenger_member else 0.03
        elif filter_damage:
            excess20 = 0.08 if incumbent_member else 0.01
            excess60 = 0.15 if incumbent_member else 0.01
        else:
            excess20 = 0.02 if challenger_member else 0.04
            excess60 = 0.03 if challenger_member else 0.10
        rows.append(
            _candidate(
                rank=rank,
                signal='BUY' if rank <= 8 else 'WATCH',
                candidate_type='Stable Leader',
                rs6=300.0 - rank if incumbent_member else 100.0 - rank,
                rs3=500.0 - rank if challenger_member else float(rank),
                return6=0.5 if challenger_member else (-0.1 if incumbent_member else 0.2),
                above_sma200=challenger_member or not filter_damage,
                traded_value=1_000_000.0,
                drawdown=-0.10,
                stretch=0.10,
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
        universe_source='universe_cache:NORWAY_V2',
        snapshot=snapshot,
        as_of_date=as_of.isoformat(),
        candidate_rows=tuple(candidate_rows),
        missing_exit_rows=(),
    )


class ChallengerComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path('/tmp/tradetool_challenger_comparison_readonly.sqlite')
        self.out_dir = Path('/tmp/tradetool_challenger_comparison_output')
        self._cleanup()
        self.db_path.write_text('readonly', encoding='utf-8')

    def tearDown(self) -> None:
        self._cleanup()

    def _build(self, *, rows: tuple[dict[str, object]] | None = None, invalid: bool = False):
        source_rows = _rows() if rows is None else rows

        def builder(**kwargs):
            return _snapshot(kwargs['as_of_date'], rows=source_rows, invalid=invalid)

        return build_challenger_comparison(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2025, 11, 30),
            data_source='yahoo',
            forward_return_builder=builder,
        )

    def test_challenger_selection_filters_and_sorts(self) -> None:
        rows = (
            {'ticker': 'BBB.OL', 'above_sma200': True, 'return_6m': 0.2, 'relative_strength_6m': 0.5, 'relative_strength_3m': 0.3, 'average_traded_value_20': 1_000_000.0, 'drawdown_252': -0.1, 'distance_to_sma200': 0.1},
            {'ticker': 'AAA.OL', 'above_sma200': True, 'return_6m': 0.2, 'relative_strength_6m': 0.5, 'relative_strength_3m': 0.3, 'average_traded_value_20': 1_000_000.0, 'drawdown_252': -0.1, 'distance_to_sma200': 0.1},
            {'ticker': 'CCC.OL', 'above_sma200': False, 'return_6m': 1.0, 'relative_strength_6m': 9.0, 'relative_strength_3m': 9.0, 'average_traded_value_20': 1_000_000.0, 'drawdown_252': -0.1, 'distance_to_sma200': 0.1},
            {'ticker': 'DDD.OL', 'above_sma200': True, 'return_6m': 1.0, 'relative_strength_6m': 0.4, 'relative_strength_3m': 0.9, 'average_traded_value_20': 100.0, 'drawdown_252': -0.1, 'distance_to_sma200': 0.1},
        )
        selected = select_challenger_rs6m_trend_risk(rows, limit=3)
        self.assertEqual([row['ticker'] for row in selected], ['AAA.OL', 'BBB.OL'])

    def test_report_writes_expected_files_and_promote_decision(self) -> None:
        result = self._build()
        write_challenger_comparison_outputs(result=result, out_dir=self.out_dir)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))
        summary = json.loads((self.out_dir / 'challenger_comparison_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['incumbent_id'], BASELINE_ID)
        self.assertEqual(summary['challenger_id'], CHALLENGER_ID)
        self.assertEqual(summary['decision_recommendation'], DECISION_PROMOTE)

    def test_group_summary_includes_incumbent_challenger_and_context(self) -> None:
        result = self._build()
        groups = {row['group'] for row in result.group_summary_rows}
        self.assertIn(GROUP_INCUMBENT, groups)
        self.assertIn(GROUP_CHALLENGER, groups)
        self.assertIn('v2_raw_top10_context_only', groups)

    def test_overlap_is_calculated(self) -> None:
        result = self._build()
        overlap = next(row for row in result.overlap_rows if row['forward_window_trading_days'] == 20)
        self.assertEqual(overlap['overlap_count'], 0)
        self.assertIn('T01.OL', overlap['incumbent_only_tickers'])
        self.assertIn('T11.OL', overlap['challenger_only_tickers'])

    def test_keep_incumbent_decision_when_challenger_does_not_beat(self) -> None:
        rows = []
        for source in _rows(challenger_wins=False):
            row = dict(source)
            features = dict(row['_features'])
            if row['raw_rank'] <= 10:
                features['return_6m'] = 0.5
                features['above_sma200'] = True
            row['_features'] = features
            rows.append(row)
        result = self._build(rows=tuple(rows))
        self.assertEqual(result.decision_recommendation, DECISION_KEEP_INCUMBENT)

    def test_filter_damage_decision_when_filters_remove_winners(self) -> None:
        result = self._build(rows=_rows(challenger_wins=False, filter_damage=True))
        self.assertEqual(result.decision_recommendation, DECISION_FILTER_DAMAGE)

    def test_blocked_decision_on_invalid_snapshots(self) -> None:
        result = self._build(invalid=True)
        self.assertEqual(result.decision_recommendation, 'blocked_data_or_alignment_issue')

    def test_cli_requires_explicit_db_path_and_writes_outputs(self) -> None:
        parser = build_argument_parser()
        db_action = next(action for action in parser._actions if '--db-path' in action.option_strings)
        self.assertTrue(db_action.required)
        self.assertIsNone(db_action.default)

        import tradetool.diagnostics.challenger_comparison_cli as cli_module
        original = cli_module.build_challenger_comparison

        def builder(**kwargs):
            return build_challenger_comparison(
                **kwargs,
                forward_return_builder=lambda **inner_kwargs: _snapshot(inner_kwargs['as_of_date'], rows=_rows()),
            )

        try:
            cli_module.build_challenger_comparison = builder
            exit_code = challenger_cli_main([
                '--db-path', str(self.db_path),
                '--universe-id', 'NORWAY_V2',
                '--benchmark-ticker', 'OSEBX.OL',
                '--rebalance-start-date', '2025-09-30',
                '--rebalance-end-date', '2025-11-30',
                '--data-source', 'yahoo',
                '--out-dir', str(self.out_dir),
            ])
        finally:
            cli_module.build_challenger_comparison = original
        self.assertEqual(exit_code, 0)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))

    def test_no_production_ui_policy_ml_or_holdings_changes(self) -> None:
        self.assertEqual(BASELINE_RANKING_ENGINE_ID, 'baseline_v0_price_volume_rs')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(MAX_BUY_RAW_RANK, 20)
        self.assertEqual(MIN_ACCEPTABLE_TRADED_VALUE, 750_000.0)
        source = Path('src/tradetool/diagnostics/challenger_comparison.py').read_text(encoding='utf-8').lower()
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

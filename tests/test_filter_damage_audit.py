from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from tradetool.diagnostics.filter_damage_audit import (
    DECISION_INVESTIGATE_SORTING,
    DECISION_REMOVE_FILTERS,
    EXPECTED_REPORT_FILES,
    build_filter_damage_audit,
    write_filter_damage_audit_outputs,
)
from tradetool.diagnostics.filter_damage_audit_cli import build_argument_parser
from tradetool.diagnostics.filter_damage_audit_cli import main as filter_damage_cli_main
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import MAX_BUY_RAW_RANK, MIN_ACCEPTABLE_TRADED_VALUE, TRADE_POLICY_ENGINE_ID
from tradetool.ranking.baseline import BASELINE_RANKING_ENGINE_ID


def _candidate(
    *,
    rank: int,
    rs6: float,
    return6: float,
    excess20: float,
    excess60: float,
    above_sma200: bool = True,
    rs3: float = 0.1,
    traded_value: float = 1_000_000.0,
    drawdown: float = -0.10,
    stretch: float = 0.10,
) -> dict[str, object]:
    ticker = f'T{rank:02d}.OL'
    return {
        'ticker': ticker,
        'raw_rank': rank,
        'raw_score': float(100 - rank),
        'trade_signal': 'BUY' if rank <= 8 else 'WATCH',
        'candidate_type': 'Stable Leader',
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


def _rows(*, removed_winners: bool = True) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for rank in range(1, 31):
        incumbent_member = rank <= 10
        if incumbent_member and rank <= 5:
            excess = 0.14 if removed_winners else -0.04
            rows.append(
                _candidate(
                    rank=rank,
                    rs6=300.0 - rank,
                    return6=-0.10,
                    above_sma200=False,
                    excess20=excess,
                    excess60=excess,
                )
            )
        elif incumbent_member:
            rows.append(_candidate(rank=rank, rs6=300.0 - rank, return6=0.20, excess20=0.01, excess60=0.01))
        else:
            rows.append(_candidate(rank=rank, rs6=100.0 - rank, return6=0.40, rs3=1.0, excess20=0.02, excess60=0.02))
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


class FilterDamageAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path('/tmp/tradetool_filter_damage_readonly.sqlite')
        self.out_dir = Path('/tmp/tradetool_filter_damage_output')
        self._cleanup()
        self.db_path.write_text('readonly', encoding='utf-8')

    def tearDown(self) -> None:
        self._cleanup()

    def _build(self, *, rows: tuple[dict[str, object]] | None = None, invalid: bool = False):
        source_rows = _rows() if rows is None else rows

        def builder(**kwargs):
            return _snapshot(kwargs['as_of_date'], rows=source_rows, invalid=invalid)

        return build_filter_damage_audit(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2025, 11, 30),
            data_source='yahoo',
            forward_return_builder=builder,
        )

    def test_report_writes_expected_files_and_remove_filters_decision(self) -> None:
        result = self._build()
        write_filter_damage_audit_outputs(result=result, out_dir=self.out_dir)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))
        summary = json.loads((self.out_dir / 'filter_damage_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['decision_recommendation'], DECISION_REMOVE_FILTERS)

    def test_removed_incumbent_pick_rows_include_gates_and_forward_returns(self) -> None:
        result = self._build()
        removed = [row for row in result.removed_pick_rows if row['removed_by_challenger_filters']]
        self.assertTrue(removed)
        self.assertIn('above_sma200', removed[0]['failing_gates'])
        self.assertIn('return_6m_positive', removed[0]['failing_gates'])
        self.assertIn('60d_net_excess_return', removed[0])
        self.assertIn('trade_signal', removed[0])
        self.assertIn('candidate_type', removed[0])

    def test_damage_by_gate_counts_removed_winners(self) -> None:
        result = self._build()
        by_gate = {row['gate']: row for row in result.by_gate_rows if row['row_type'] == 'gate'}
        self.assertGreater(by_gate['above_sma200']['60d_removed_winner_count'], 0)
        self.assertGreater(by_gate['return_6m_positive']['60d_removed_winner_count'], 0)

    def test_monthly_summary_counts_removed_and_included(self) -> None:
        result = self._build()
        first = result.monthly_rows[0]
        self.assertEqual(first['incumbent_pick_count'], 10)
        self.assertEqual(first['removed_incumbent_pick_count'], 5)
        self.assertEqual(first['challenger_included_incumbent_pick_count'], 5)

    def test_sorting_diagnosis_when_filters_do_not_remove_winners(self) -> None:
        result = self._build(rows=_rows(removed_winners=False))
        self.assertEqual(result.filter_vs_sorting_diagnosis, 'sorting_not_filters')
        self.assertEqual(result.decision_recommendation, DECISION_INVESTIGATE_SORTING)

    def test_methodology_fix_decision_when_snapshot_invalid(self) -> None:
        result = self._build(invalid=True)
        self.assertEqual(result.decision_recommendation, 'fix_filter_damage_methodology')

    def test_cli_requires_db_path_and_writes_outputs(self) -> None:
        parser = build_argument_parser()
        db_action = next(action for action in parser._actions if '--db-path' in action.option_strings)
        self.assertTrue(db_action.required)

        import tradetool.diagnostics.filter_damage_audit_cli as cli_module
        original = cli_module.build_filter_damage_audit

        def builder(**kwargs):
            return build_filter_damage_audit(
                **kwargs,
                forward_return_builder=lambda **inner_kwargs: _snapshot(inner_kwargs['as_of_date'], rows=_rows()),
            )

        try:
            cli_module.build_filter_damage_audit = builder
            exit_code = filter_damage_cli_main([
                '--db-path', str(self.db_path),
                '--universe-id', 'NORWAY_V2',
                '--benchmark-ticker', 'OSEBX.OL',
                '--rebalance-start-date', '2025-09-30',
                '--rebalance-end-date', '2025-11-30',
                '--data-source', 'yahoo',
                '--out-dir', str(self.out_dir),
            ])
        finally:
            cli_module.build_filter_damage_audit = original
        self.assertEqual(exit_code, 0)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))

    def test_no_production_ui_policy_ml_or_holdings_changes(self) -> None:
        self.assertEqual(BASELINE_RANKING_ENGINE_ID, 'baseline_v0_price_volume_rs')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(MAX_BUY_RAW_RANK, 20)
        self.assertEqual(MIN_ACCEPTABLE_TRADED_VALUE, 750_000.0)
        source = Path('src/tradetool/diagnostics/filter_damage_audit.py').read_text(encoding='utf-8').lower()
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

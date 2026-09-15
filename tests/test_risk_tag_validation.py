from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from tradetool.diagnostics.incumbent_baseline import BASELINE_ID
from tradetool.diagnostics.risk_tag_validation import (
    DECISION_DO_NOT_FILTER,
    DECISION_FIX_METHODOLOGY,
    DECISION_INVESTIGATE_TAG_FILTER,
    DECISION_KEEP_INFORMATIONAL,
    EXPECTED_REPORT_FILES,
    GROUP_SELECTED_TOP10,
    build_risk_tag_validation,
    write_risk_tag_validation_outputs,
)
from tradetool.diagnostics.risk_tag_validation_cli import build_argument_parser
from tradetool.diagnostics.risk_tag_validation_cli import main as risk_tag_cli_main


def _candidate(
    *,
    rank: int,
    rs6: float,
    risk: str,
    excess20: float,
    excess60: float,
) -> dict[str, object]:
    ticker = f'T{rank:02d}.OL'
    high_risk = risk == 'HIGH'
    medium_risk = risk == 'MEDIUM'
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
        '20d_missing_reason': '',
        '60d_complete': True,
        '60d_return': excess60 + 0.04,
        '60d_benchmark_return': 0.04,
        '60d_excess_return': excess60,
        '60d_missing_reason': '',
        '_features': {
            'ticker': ticker,
            'return_3m': -0.02 if medium_risk else 0.05,
            'return_6m': 0.5,
            'relative_strength_3m': -0.01 if medium_risk else 0.05,
            'relative_strength_6m': rs6,
            'above_sma200': not high_risk,
            'average_traded_value_20': 2_000_000.0,
            'drawdown_252': -0.60 if high_risk else -0.10,
            'volatility_63': 0.06 if high_risk else 0.02,
            'distance_to_sma200': 0.10,
        },
    }


def _rows(*, high_loses: bool = False, high_wins: bool = False) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for rank in range(1, 16):
        risk = 'HIGH' if rank <= 5 else ('MEDIUM' if rank <= 8 else 'LOW')
        if high_wins:
            excess20 = 0.08 if risk == 'HIGH' else 0.01
            excess60 = 0.15 if risk == 'HIGH' else 0.02
        elif high_loses:
            excess20 = -0.04 if risk == 'HIGH' else 0.05
            excess60 = -0.06 if risk == 'HIGH' else 0.08
        else:
            excess20 = 0.04
            excess60 = 0.06
        rows.append(_candidate(rank=rank, rs6=float(100 - rank), risk=risk, excess20=excess20, excess60=excess60))
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


class RiskTagValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path('/tmp/tradetool_risk_tag_validation_readonly.sqlite')
        self.out_dir = Path('/tmp/tradetool_risk_tag_validation_output')
        self._cleanup()
        self.db_path.write_text('readonly', encoding='utf-8')

    def tearDown(self) -> None:
        self._cleanup()

    def _build(self, *, rows: tuple[dict[str, object]] | None = None, invalid: bool = False):
        source_rows = _rows() if rows is None else rows

        def builder(**kwargs):
            return _snapshot(kwargs['as_of_date'], rows=source_rows, invalid=invalid)

        return build_risk_tag_validation(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date=date(2025, 9, 30),
            rebalance_end_date=date(2025, 11, 30),
            data_source='yahoo',
            forward_return_builder=builder,
        )

    def test_report_writes_expected_files_and_keeps_tags_informational(self) -> None:
        result = self._build()
        write_risk_tag_validation_outputs(result=result, out_dir=self.out_dir)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))
        summary = json.loads((self.out_dir / 'risk_tag_validation_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['incumbent_id'], BASELINE_ID)
        self.assertEqual(summary['decision_recommendation'], DECISION_KEEP_INFORMATIONAL)
        self.assertFalse(summary['leakage_controls']['risk_tags_used_to_change_selection'])

    def test_level_and_tag_rows_are_calculated_for_selected_top10(self) -> None:
        result = self._build()
        high_60 = next(
            row for row in result.level_rows
            if row['scope'] == GROUP_SELECTED_TOP10 and row['risk_level'] == 'HIGH' and row['forward_window_trading_days'] == 60
        )
        self.assertEqual(high_60['pick_count'], 15)
        self.assertEqual(high_60['complete_count'], 15)
        tag_names = {row['risk_tag'] for row in result.tag_rows if row['scope'] == GROUP_SELECTED_TOP10}
        self.assertIn('deep_drawdown', tag_names)
        self.assertIn('negative_3m_return', tag_names)
        self.assertIn('no_risk_tag', tag_names)

    def test_harmful_high_risk_decision_investigates_specific_filter(self) -> None:
        result = self._build(rows=_rows(high_loses=True))
        self.assertEqual(result.decision_recommendation, DECISION_INVESTIGATE_TAG_FILTER)
        self.assertIn('deep_drawdown', result.risk_summary['harmful_tags_60d'])

    def test_anti_predictive_high_risk_decision_does_not_filter(self) -> None:
        result = self._build(rows=_rows(high_wins=True))
        self.assertEqual(result.decision_recommendation, DECISION_DO_NOT_FILTER)

    def test_invalid_snapshot_returns_methodology_fix(self) -> None:
        result = self._build(invalid=True)
        self.assertEqual(result.decision_recommendation, DECISION_FIX_METHODOLOGY)

    def test_cli_requires_explicit_db_path_and_writes_outputs(self) -> None:
        parser = build_argument_parser()
        db_action = next(action for action in parser._actions if '--db-path' in action.option_strings)
        self.assertTrue(db_action.required)
        self.assertIsNone(db_action.default)

        import tradetool.diagnostics.risk_tag_validation_cli as cli_module
        original = cli_module.build_risk_tag_validation

        def builder(**kwargs):
            return build_risk_tag_validation(
                **kwargs,
                forward_return_builder=lambda **inner_kwargs: _snapshot(inner_kwargs['as_of_date'], rows=_rows()),
            )

        try:
            cli_module.build_risk_tag_validation = builder
            exit_code = risk_tag_cli_main([
                '--db-path', str(self.db_path),
                '--universe-id', 'NORWAY_V2',
                '--benchmark-ticker', 'OSEBX.OL',
                '--rebalance-start-date', '2025-09-30',
                '--rebalance-end-date', '2025-11-30',
                '--data-source', 'yahoo',
                '--out-dir', str(self.out_dir),
            ])
        finally:
            cli_module.build_risk_tag_validation = original
        self.assertEqual(exit_code, 0)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))

    def test_no_selection_ranking_ui_ml_or_holdings_changes(self) -> None:
        source = Path('src/tradetool/diagnostics/risk_tag_validation.py').read_text(encoding='utf-8').lower()
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

from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.data.market_data_schema import MarketDataRow, initialize_market_data_schema, write_market_data_rows_for_test
from tradetool.ranking.incumbent_screener import (
    EXPECTED_REPORT_FILES,
    build_incumbent_screener,
    select_incumbent_candidates,
    write_incumbent_screener_outputs,
)
from tradetool.ranking.incumbent_screener_cli import build_argument_parser
from tradetool.ranking.incumbent_screener_cli import main as incumbent_screener_cli_main


def _market_row(*, ticker: str, price_date: date, close: float, volume: float = 2_000_000.0) -> MarketDataRow:
    return MarketDataRow(
        ticker=ticker,
        price_date=price_date.isoformat(),
        raw_open=close,
        raw_high=close + 1.0,
        raw_low=max(0.01, close - 1.0),
        raw_close=close,
        adjusted_close=close,
        volume=volume,
        data_source='yahoo',
        created_at_utc='2026-09-09T00:00:00Z',
        updated_at_utc='2026-09-09T00:00:00Z',
    )


def _seed_market_db(path: Path) -> None:
    initialize_market_data_schema(path)
    start = date(2025, 1, 1)
    rows: list[MarketDataRow] = []
    for index in range(270):
        day = start + timedelta(days=index)
        rows.append(_market_row(ticker='AAA.OL', price_date=day, close=100.0 + index * 1.0))
        rows.append(_market_row(ticker='BBB.OL', price_date=day, close=100.0 + index * 0.7))
        rows.append(_market_row(ticker='CCC.OL', price_date=day, close=100.0 + index * 0.3))
        rows.append(_market_row(ticker='DDD.OL', price_date=day, close=100.0 + index * 0.1, volume=500_000.0))
        rows.append(_market_row(ticker='OSEBX.OL', price_date=day, close=100.0 + index * 0.2))
    write_market_data_rows_for_test(db_path=path, rows=rows, allow_test_db_write=True)


def _seed_universe_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE universe_cache (universe_key TEXT PRIMARY KEY, tickers_json TEXT, source_label TEXT, updated_at TEXT)')
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('NORWAY_V2', json.dumps(['CCC.OL', 'AAA.OL', 'OSEBX.OL', 'BBB.OL', 'MISSING.OL', 'DDD.OL']), 'unit', '2026-09-09T00:00:00Z'),
        )


class IncumbentScreenerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path('/tmp/tradetool_incumbent_screener_unit.sqlite')
        self.universe_path = Path('/tmp/tradetool_incumbent_screener_universe.sqlite')
        self.out_dir = Path('/tmp/tradetool_incumbent_screener_output')
        self._cleanup()

    def tearDown(self) -> None:
        self._cleanup()

    def test_pure_selector_uses_6m_relative_strength_then_ticker_and_marks_reasons(self) -> None:
        rows = (
            {'ticker': 'CCC.OL', 'relative_strength_6m': 0.30, 'average_traded_value_20': 2_000_000.0},
            {'ticker': 'BBB.OL', 'relative_strength_6m': 0.50, 'average_traded_value_20': 100_000.0},
            {'ticker': 'AAA.OL', 'relative_strength_6m': 0.50, 'average_traded_value_20': 2_000_000.0},
            {'ticker': 'DDD.OL', 'relative_strength_6m': None},
        )
        selected = select_incumbent_candidates(rows, top_n=2)
        self.assertEqual([row['ticker'] for row in selected], ['AAA.OL', 'BBB.OL', 'CCC.OL'])
        self.assertEqual([row['incumbent_rank'] for row in selected], [1, 2, 3])
        self.assertEqual([row['incumbent_selected'] for row in selected], [True, True, False])
        self.assertEqual(selected[-1]['incumbent_selection_reason'], 'outside_top_n_relative_strength_6m')
        self.assertEqual(selected[1]['risk_level'], 'HIGH')
        self.assertIn('very_low_liquidity', selected[1]['risk_tags'])

    def test_build_screener_uses_snapshot_rows_and_writes_expected_outputs(self) -> None:
        _seed_market_db(self.db_path)
        _seed_universe_db(self.universe_path)
        result = build_incumbent_screener(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            top_n=3,
            universe_db_path=self.universe_path,
        )
        self.assertEqual(result.baseline_id, 'incumbent_naive_rs_6m_top_10_v0')
        self.assertEqual(result.universe_source, 'universe_cache:NORWAY_V2')
        self.assertEqual(result.requested_stock_ticker_count, 5)
        self.assertEqual(result.selected_count, 3)
        self.assertEqual([row['ticker'] for row in result.top_candidates], ['AAA.OL', 'BBB.OL', 'CCC.OL'])
        self.assertIn('MISSING.OL', {row['ticker'] for row in result.rejections})
        self.assertLessEqual(date.fromisoformat(result.effective_feature_date or '9999-01-01'), date(2025, 9, 27))

        write_incumbent_screener_outputs(result=result, out_dir=self.out_dir)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))
        summary = json.loads((self.out_dir / 'incumbent_screener_summary.json').read_text(encoding='utf-8'))
        self.assertFalse(summary['guardrails']['screener_ui_changed'])
        self.assertEqual(summary['selection_rule'], 'feature-complete stocks sorted by relative_strength_6m descending, ticker ascending')
        candidates = (self.out_dir / 'incumbent_screener_top_candidates.csv').read_text(encoding='utf-8')
        self.assertIn('relative_strength_6m', candidates)
        self.assertIn('average_traded_value_20', candidates)
        self.assertIn('risk_level', candidates)
        self.assertIn('risk_tags', candidates)
        self.assertIn('risk_explanation_no', candidates)

    def test_cli_requires_expected_arguments_and_writes_outputs(self) -> None:
        _seed_market_db(self.db_path)
        _seed_universe_db(self.universe_path)
        parser = build_argument_parser()
        required = {option for action in parser._actions if action.required for option in action.option_strings}
        self.assertIn('--db-path', required)
        self.assertIn('--as-of-date', required)
        exit_code = incumbent_screener_cli_main([
            '--db-path', str(self.db_path),
            '--universe-id', 'NORWAY_V2',
            '--benchmark-ticker', 'OSEBX.OL',
            '--as-of-date', '2025-09-27',
            '--data-source', 'yahoo',
            '--top-n', '2',
            '--universe-db-path', str(self.universe_path),
            '--out-dir', str(self.out_dir),
        ])
        self.assertEqual(exit_code, 0)
        summary = json.loads((self.out_dir / 'incumbent_screener_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['selected_count'], 2)

    def test_module_has_no_ui_ml_or_holdings_dependency(self) -> None:
        source = Path('src/tradetool/ranking/incumbent_screener.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('streamlit', source)
        self.assertNotIn('ml_runs', source)
        self.assertNotIn('tradetool.holdings', source)
        self.assertNotIn('write_market_data', source)

    def _cleanup(self) -> None:
        for path in (self.db_path, self.universe_path):
            if path.exists():
                path.unlink()
        if self.out_dir.exists():
            for child in self.out_dir.iterdir():
                child.unlink()
            self.out_dir.rmdir()


if __name__ == '__main__':
    unittest.main()

from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.data import load_price_history_v2_for_tickers
from tradetool.data.market_data_schema import MarketDataRow, initialize_market_data_schema, write_market_data_rows_for_test
from tradetool.diagnostics.holdout_snapshot import (
    EXPECTED_REPORT_FILES,
    build_holdout_snapshot,
    load_stock_tickers,
    write_holdout_snapshot_outputs,
)
from tradetool.diagnostics.holdout_snapshot_cli import build_argument_parser
from tradetool.diagnostics.holdout_snapshot_cli import main as holdout_snapshot_cli_main
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import MAX_BUY_RAW_RANK, MIN_ACCEPTABLE_TRADED_VALUE, TRADE_POLICY_ENGINE_ID
from tradetool.ranking.baseline import BASELINE_RANKING_ENGINE_ID


def _market_row(
    *,
    ticker: str,
    price_date: date,
    close: float,
    volume: float = 2_000_000.0,
    data_source: str = 'yahoo',
) -> MarketDataRow:
    return MarketDataRow(
        ticker=ticker,
        price_date=price_date.isoformat(),
        raw_open=close,
        raw_high=close + 1.0,
        raw_low=max(0.01, close - 1.0),
        raw_close=close,
        adjusted_close=close,
        volume=volume,
        data_source=data_source,
        created_at_utc='2026-09-09T00:00:00Z',
        updated_at_utc='2026-09-09T00:00:00Z',
    )


def _seed_snapshot_db(path: Path, *, include_benchmark: bool = True) -> None:
    initialize_market_data_schema(path)
    start = date(2025, 1, 1)
    rows: list[MarketDataRow] = []
    for index in range(270):
        day = start + timedelta(days=index)
        rows.append(_market_row(ticker='DNB.OL', price_date=day, close=100.0 + index * 0.6))
        rows.append(_market_row(ticker='NONG.OL', price_date=day, close=90.0 + index * 0.5))
        if include_benchmark:
            rows.append(_market_row(ticker='OSEBX.OL', price_date=day, close=300.0 + index * 0.2))
    future_day = date(2026, 1, 1)
    rows.append(_market_row(ticker='DNB.OL', price_date=future_day, close=999.0))
    rows.append(_market_row(ticker='OSEBX.OL', price_date=future_day, close=999.0))
    write_market_data_rows_for_test(db_path=path, rows=rows, allow_test_db_write=True)


def _seed_sp500_snapshot_db(path: Path) -> None:
    initialize_market_data_schema(path)
    start = date(2025, 1, 1)
    rows: list[MarketDataRow] = []
    for index in range(270):
        day = start + timedelta(days=index)
        rows.append(_market_row(ticker='AAPL', price_date=day, close=150.0 + index * 0.6))
        rows.append(_market_row(ticker='MSFT', price_date=day, close=250.0 + index * 0.5))
        rows.append(_market_row(ticker='NVDA', price_date=day, close=120.0 + index * 0.8))
        rows.append(_market_row(ticker='^GSPC', price_date=day, close=4000.0 + index * 1.5))
    write_market_data_rows_for_test(db_path=path, rows=rows, allow_test_db_write=True)


def _seed_universe_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE universe_cache (universe_key TEXT PRIMARY KEY, tickers_json TEXT, source_label TEXT, updated_at TEXT)')
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('NORWAY_V2', json.dumps(['DNB.OL', 'NONG.OL', 'MISSING.OL']), 'unit', '2026-09-09T00:00:00Z'),
        )
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('NORWAY_WITH_BENCHMARK', json.dumps(['DNB.OL', 'OSEBX.OL', 'NONG.OL', '']), 'unit', '2026-09-09T00:00:00Z'),
        )
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('SP500', json.dumps(['AAPL', 'MSFT', '^GSPC', 'NVDA', '']), 'unit', '2026-09-09T00:00:00Z'),
        )


class HoldoutSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path('/tmp/tradetool_holdout_snapshot_unit.sqlite')
        self.universe_path = Path('/tmp/tradetool_holdout_snapshot_universe.sqlite')
        self.out_dir = Path('/tmp/tradetool_holdout_snapshot_output')
        self._cleanup()

    def tearDown(self) -> None:
        self._cleanup()

    def test_snapshot_excludes_rows_after_as_of_date_for_tickers_and_benchmark(self) -> None:
        _seed_snapshot_db(self.db_path)
        loaded = load_price_history_v2_for_tickers(
            db_path=str(self.db_path),
            tickers=['DNB.OL', 'OSEBX.OL'],
            data_source='yahoo',
            max_price_date=date(2025, 9, 27),
        )
        self.assertLessEqual(loaded.rows_by_ticker['DNB.OL'][-1].price_date, date(2025, 9, 27))
        self.assertLessEqual(loaded.rows_by_ticker['OSEBX.OL'][-1].price_date, date(2025, 9, 27))
        self.assertNotEqual(loaded.rows_by_ticker['DNB.OL'][-1].close, 999.0)

    def test_snapshot_generates_ranked_candidates_with_feature_date_not_after_as_of(self) -> None:
        _seed_snapshot_db(self.db_path)
        _seed_universe_db(self.universe_path)
        result = build_holdout_snapshot(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            universe_db_path=self.universe_path,
        )
        self.assertTrue(result.snapshot_valid)
        self.assertEqual(result.requested_stock_ticker_count, 3)
        self.assertEqual(result.present_ticker_count, 2)
        self.assertEqual(result.missing_ticker_count, 1)
        self.assertEqual(result.missing_tickers, ('MISSING.OL',))
        self.assertEqual(result.ranked_count, 2)
        self.assertLessEqual(date.fromisoformat(result.effective_feature_date or '9999-01-01'), date(2025, 9, 27))
        self.assertTrue(all(date.fromisoformat(str(row['latest_price_date'])) <= date(2025, 9, 27) for row in result.ranked_rows))

    def test_load_stock_tickers_keeps_ose_tickers_and_excludes_ose_benchmark(self) -> None:
        _seed_universe_db(self.universe_path)
        tickers, source = load_stock_tickers(
            universe_id='NORWAY_WITH_BENCHMARK',
            universe_db_path=self.universe_path,
            benchmark_ticker='OSEBX.OL',
        )
        self.assertEqual(tickers, ('DNB.OL', 'NONG.OL'))
        self.assertEqual(source, 'universe_cache:NORWAY_WITH_BENCHMARK')

    def test_load_stock_tickers_supports_sp500_tickers_and_excludes_benchmark(self) -> None:
        _seed_universe_db(self.universe_path)
        tickers, source = load_stock_tickers(
            universe_id='SP500',
            universe_db_path=self.universe_path,
            benchmark_ticker='^GSPC',
        )
        self.assertEqual(tickers, ('AAPL', 'MSFT', 'NVDA'))
        self.assertEqual(source, 'universe_cache:SP500')

    def test_snapshot_generates_ranked_candidates_for_non_ol_universe(self) -> None:
        _seed_sp500_snapshot_db(self.db_path)
        _seed_universe_db(self.universe_path)
        result = build_holdout_snapshot(
            db_path=self.db_path,
            universe_id='SP500',
            benchmark_ticker='^GSPC',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            universe_db_path=self.universe_path,
        )
        self.assertTrue(result.snapshot_valid)
        self.assertEqual(result.requested_stock_ticker_count, 3)
        self.assertEqual(result.present_ticker_count, 3)
        self.assertEqual(result.ranked_count, 3)
        self.assertNotIn('^GSPC', {row['ticker'] for row in result.ranked_rows})

    def test_missing_benchmark_makes_snapshot_invalid(self) -> None:
        _seed_snapshot_db(self.db_path, include_benchmark=False)
        _seed_universe_db(self.universe_path)
        result = build_holdout_snapshot(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            universe_db_path=self.universe_path,
        )
        self.assertFalse(result.snapshot_valid)
        self.assertIn('missing_benchmark_data', result.invalid_reasons)

    def test_no_forward_ml_or_holdings_fields_are_output(self) -> None:
        _seed_snapshot_db(self.db_path)
        _seed_universe_db(self.universe_path)
        result = build_holdout_snapshot(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            universe_db_path=self.universe_path,
        )
        forbidden = ('forward_return', 'ml_score', 'holdings_signal', 'ownership')
        for row in result.ranked_rows:
            lowered = {key.lower() for key in row}
            for field in forbidden:
                self.assertFalse(any(field in key for key in lowered))
        self.assertFalse(result.to_summary_dict()['leakage_controls']['forward_returns_calculated'])

    def test_output_files_are_written(self) -> None:
        _seed_snapshot_db(self.db_path)
        _seed_universe_db(self.universe_path)
        result = build_holdout_snapshot(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            universe_db_path=self.universe_path,
        )
        write_holdout_snapshot_outputs(result=result, out_dir=self.out_dir)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))
        summary = json.loads((self.out_dir / 'holdout_snapshot_summary.json').read_text(encoding='utf-8'))
        self.assertTrue(summary['snapshot_valid'])

    def test_cli_writes_outputs(self) -> None:
        _seed_snapshot_db(self.db_path)
        _seed_universe_db(self.universe_path)
        exit_code = holdout_snapshot_cli_main([
            '--db-path', str(self.db_path),
            '--universe-id', 'NORWAY_V2',
            '--benchmark-ticker', 'OSEBX.OL',
            '--as-of-date', '2025-09-27',
            '--data-source', 'yahoo',
            '--universe-db-path', str(self.universe_path),
            '--out-dir', str(self.out_dir),
        ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))

    def test_cli_requires_explicit_db_path(self) -> None:
        parser = build_argument_parser()
        db_action = next(action for action in parser._actions if '--db-path' in action.option_strings)
        self.assertTrue(db_action.required)
        self.assertIsNone(db_action.default)

    def test_no_db_writes_occur(self) -> None:
        _seed_snapshot_db(self.db_path)
        _seed_universe_db(self.universe_path)
        before = self.db_path.stat().st_mtime_ns
        build_holdout_snapshot(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            universe_db_path=self.universe_path,
        )
        self.assertEqual(before, self.db_path.stat().st_mtime_ns)

    def test_ranking_policy_and_candidate_type_constants_are_unchanged(self) -> None:
        self.assertEqual(BASELINE_RANKING_ENGINE_ID, 'baseline_v0_price_volume_rs')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(MAX_BUY_RAW_RANK, 20)
        self.assertEqual(MIN_ACCEPTABLE_TRADED_VALUE, 750_000.0)

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

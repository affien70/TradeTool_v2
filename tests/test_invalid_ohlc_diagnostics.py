from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.diagnostics.invalid_ohlc import build_invalid_ohlc_diagnostics, write_invalid_ohlc_outputs
from tradetool.diagnostics.invalid_ohlc_cli import main as invalid_ohlc_cli_main
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import TRADE_POLICY_ENGINE_ID


def _create_base_schema(connection: sqlite3.Connection) -> None:
    connection.execute('CREATE TABLE price_history (ticker TEXT, price_date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, updated_at TEXT)')
    connection.execute('CREATE TABLE universe_cache (universe_key TEXT PRIMARY KEY, tickers_json TEXT, source_label TEXT, updated_at TEXT)')


def _insert_price(
    connection: sqlite3.Connection,
    *,
    ticker: str,
    price_date: str,
    open_value: float | None,
    high_value: float | None,
    low_value: float | None,
    close_value: float | None,
    volume_value: float | None,
) -> None:
    connection.execute(
        'INSERT INTO price_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (ticker, price_date, open_value, high_value, low_value, close_value, volume_value, f'{price_date}T16:00:00Z'),
    )


def _insert_series(
    connection: sqlite3.Connection,
    *,
    ticker: str,
    start_date: date,
    row_count: int,
    invalid_offsets: dict[int, dict[str, float | None]] | None = None,
) -> None:
    invalid_offsets = invalid_offsets or {}
    for offset in range(row_count):
        price_day = start_date + timedelta(days=offset)
        base = 100.0 + offset
        values = {
            'open_value': base,
            'high_value': base + 1.0,
            'low_value': base - 1.0,
            'close_value': base,
            'volume_value': 1000.0 + offset,
        }
        values.update(invalid_offsets.get(offset, {}))
        _insert_price(connection, ticker=ticker, price_date=price_day.isoformat(), **values)


def _build_invalid_fixture(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        _create_base_schema(connection)
        _insert_series(
            connection,
            ticker='AAA.OL',
            start_date=date(2025, 1, 1),
            row_count=260,
            invalid_offsets={
                0: {'high_value': 98.0, 'low_value': 99.0},
                259: {'high_value': 358.0, 'close_value': 359.0},
            },
        )
        _insert_series(
            connection,
            ticker='BBB.OL',
            start_date=date(2025, 1, 1),
            row_count=260,
            invalid_offsets={
                20: {'open_value': None},
                40: {'volume_value': -5.0},
            },
        )
        _insert_series(connection, ticker='CCC.OL', start_date=date(2025, 1, 1), row_count=260)
        _insert_series(connection, ticker='^OSEAX', start_date=date(2025, 1, 1), row_count=260)
        _insert_price(
            connection,
            ticker='OUTSIDE.US',
            price_date='2025-12-31',
            open_value=10.0,
            high_value=9.0,
            low_value=11.0,
            close_value=10.0,
            volume_value=1.0,
        )
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('NORWAY_V2', json.dumps(['AAA.OL', 'BBB.OL', 'CCC.OL']), 'fixture', '2026-08-17T00:00:00Z'),
        )


def _build_historical_only_fixture(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        _create_base_schema(connection)
        _insert_series(
            connection,
            ticker='AAA.OL',
            start_date=date(2025, 1, 1),
            row_count=260,
            invalid_offsets={0: {'high_value': 98.0, 'low_value': 99.0}},
        )
        _insert_series(connection, ticker='BBB.OL', start_date=date(2025, 1, 1), row_count=260)
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('NORWAY_V2', json.dumps(['AAA.OL', 'BBB.OL']), 'fixture', '2026-08-17T00:00:00Z'),
        )


class InvalidOhlcDiagnosticsTests(unittest.TestCase):
    def test_cli_writes_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'invalid_output'
            _build_invalid_fixture(db_path)
            exit_code = invalid_ohlc_cli_main(['--db-path', str(db_path), '--universe-id', 'NORWAY_V2', '--out-dir', str(out_dir)])
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                sorted(path.name for path in out_dir.iterdir()),
                [
                    'invalid_ohlc_by_reason.csv',
                    'invalid_ohlc_by_ticker.csv',
                    'invalid_ohlc_rows.csv',
                    'invalid_ohlc_summary.json',
                    'invalid_ohlc_summary.md',
                ],
            )

    def test_reason_distribution_and_ticker_scope_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_invalid_fixture(db_path)
            result = build_invalid_ohlc_diagnostics(db_path=db_path, universe_id='NORWAY_V2')
            self.assertEqual(result.invalid_row_count, 4)
            self.assertEqual(result.affected_ticker_count, 2)
            self.assertEqual(result.price_date_column, 'price_date')
            reason_counts = {row.reason_code: row.invalid_row_count for row in result.by_reason_rows}
            self.assertEqual(reason_counts['high_lower_than_low'], 1)
            self.assertEqual(reason_counts['missing_open'], 1)
            self.assertEqual(reason_counts['negative_volume'], 1)
            self.assertEqual(reason_counts['high_lower_than_close'], 2)
            self.assertNotIn('OUTSIDE.US', {row.ticker for row in result.rows})

    def test_recent_vs_historical_and_latest_252_window_are_tracked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_invalid_fixture(db_path)
            result = build_invalid_ohlc_diagnostics(db_path=db_path, universe_id='NORWAY_V2')
            self.assertEqual(result.rows_on_universe_max_date, 1)
            self.assertEqual(result.rows_with_recent_invalid_dates, 1)
            self.assertEqual(result.rows_with_historical_invalid_dates, 3)
            self.assertEqual(result.rows_within_latest_252_window, 3)
            self.assertEqual(result.tickers_within_latest_252_window, 2)
            self.assertFalse(result.likely_harmless_for_latest_252_window)

    def test_historical_only_invalid_rows_can_be_marked_likely_harmless_for_latest_252_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_historical_only_fixture(db_path)
            result = build_invalid_ohlc_diagnostics(db_path=db_path, universe_id='NORWAY_V2')
            self.assertEqual(result.invalid_row_count, 1)
            self.assertEqual(result.rows_within_latest_252_window, 0)
            self.assertTrue(result.likely_harmless_for_latest_252_window)

    def test_summary_json_and_csvs_include_expected_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'invalid_output'
            _build_invalid_fixture(db_path)
            result = build_invalid_ohlc_diagnostics(db_path=db_path, universe_id='NORWAY_V2')
            write_invalid_ohlc_outputs(result=result, out_dir=out_dir)
            summary = json.loads((out_dir / 'invalid_ohlc_summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['price_date_column'], 'price_date')
            self.assertIn('reason_distribution', summary)
            with (out_dir / 'invalid_ohlc_rows.csv').open('r', encoding='utf-8', newline='') as handle:
                row = next(csv.DictReader(handle))
            self.assertIn('reason_codes', row)
            self.assertIn('recency_bucket', row)
            self.assertIn('within_latest_252_rows', row)

    def test_no_database_writes_occur(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_invalid_fixture(db_path)
            with sqlite3.connect(db_path) as connection:
                before = connection.execute('SELECT COUNT(*) FROM price_history').fetchone()[0]
            build_invalid_ohlc_diagnostics(db_path=db_path, universe_id='NORWAY_V2')
            with sqlite3.connect(db_path) as connection:
                after = connection.execute('SELECT COUNT(*) FROM price_history').fetchone()[0]
            self.assertEqual(before, after)

    def test_no_ranking_feature_policy_candidate_type_ml_or_holdings_logic_is_changed(self) -> None:
        ranking_source = Path('src/tradetool/ranking/baseline.py').read_text(encoding='utf-8')
        candidate_source = Path('src/tradetool/policy/candidate_type.py').read_text(encoding='utf-8')
        trade_policy_source = Path('src/tradetool/policy/trade_policy.py').read_text(encoding='utf-8')
        invalid_source = Path('src/tradetool/diagnostics/invalid_ohlc.py').read_text(encoding='utf-8').lower()
        self.assertIn("BASELINE_RANKING_ENGINE_ID = 'baseline_v0_price_volume_rs'", ranking_source)
        self.assertIn("CANDIDATE_TYPE_ENGINE_ID = 'candidate_type_v0_diagnostic'", candidate_source)
        self.assertIn("TRADE_POLICY_ENGINE_ID = 'trade_policy_v1_balanced_diagnostic'", trade_policy_source)
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertNotIn('holdings_signal', invalid_source)
        self.assertNotIn('ml_score', invalid_source)

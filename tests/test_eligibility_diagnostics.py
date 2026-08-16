from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.diagnostics.eligibility import build_eligibility_diagnostics
from tradetool.diagnostics.eligibility_cli import main as eligibility_cli_main


def _insert_price_rows(
    connection: sqlite3.Connection,
    *,
    ticker: str,
    row_count: int,
    last_date: date,
    invalid_ohlc_on_last_row: bool = False,
    duplicate_last_row: bool = False,
) -> None:
    rows: list[tuple[str, str, float, float, float, float, float]] = []
    first_date = last_date - timedelta(days=row_count - 1)
    for offset in range(row_count):
        current_date = first_date + timedelta(days=offset)
        open_value = 100.0 + offset
        high_value = open_value + 2.0
        low_value = open_value - 2.0
        close_value = open_value + 1.0
        volume_value = 1000.0
        if invalid_ohlc_on_last_row and offset == row_count - 1:
            low_value = high_value + 1.0
        rows.append(
            (
                ticker,
                current_date.isoformat(),
                open_value,
                high_value,
                low_value,
                close_value,
                volume_value,
            )
        )
    connection.executemany('INSERT INTO price_history VALUES (?, ?, ?, ?, ?, ?, ?)', rows)
    if duplicate_last_row:
        connection.execute('INSERT INTO price_history VALUES (?, ?, ?, ?, ?, ?, ?)', rows[-1])


def _build_eligibility_fixture_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            'CREATE TABLE price_history (ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)'
        )
        connection.execute(
            'CREATE TABLE universe_cache (universe_key TEXT PRIMARY KEY, tickers_json TEXT, source_label TEXT, updated_at TEXT)'
        )
        max_date = date(2026, 6, 9)
        _insert_price_rows(connection, ticker='GOOD1.OL', row_count=260, last_date=max_date)
        _insert_price_rows(connection, ticker='GOOD2.OL', row_count=255, last_date=max_date)
        _insert_price_rows(connection, ticker='SHORT.OL', row_count=200, last_date=max_date)
        _insert_price_rows(connection, ticker='STALE.OL', row_count=260, last_date=max_date - timedelta(days=1))
        _insert_price_rows(connection, ticker='DUPL.OL', row_count=260, last_date=max_date, duplicate_last_row=True)
        _insert_price_rows(connection, ticker='BADOHLC.OL', row_count=260, last_date=max_date, invalid_ohlc_on_last_row=True)
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            (
                'NORWAY_V2',
                json.dumps(['GOOD1.OL', 'GOOD2.OL', 'SHORT.OL', 'STALE.OL', 'DUPL.OL', 'BADOHLC.OL', 'MISSING.OL', '']),
                'fixture',
                '2026-08-16T00:00:00Z',
            ),
        )


def _build_missing_ohlcv_fixture_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            'CREATE TABLE price_history (ticker TEXT, date TEXT, close REAL)'
        )
        connection.execute(
            'CREATE TABLE universe_cache (universe_key TEXT PRIMARY KEY, tickers_json TEXT, source_label TEXT, updated_at TEXT)'
        )
        connection.execute(
            'INSERT INTO price_history VALUES (?, ?, ?)',
            ('ONLY.OL', '2026-06-09', 10.0),
        )
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('NORWAY_V2', json.dumps(['ONLY.OL']), 'fixture', '2026-08-16T00:00:00Z'),
        )


class EligibilityDiagnosticsTests(unittest.TestCase):
    def test_all_tickers_with_enough_rows_and_fresh_dates_become_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_eligibility_fixture_db(db_path)
            result = build_eligibility_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                explicit_tickers=['GOOD1.OL', 'GOOD2.OL'],
            )
            self.assertEqual(result.universe_source, 'explicit_ticker_list')
            self.assertEqual(result.eligible_count, 2)
            self.assertEqual(result.rejected_count, 0)
            self.assertTrue(all(row.eligible for row in result.results))

    def test_missing_market_data_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_eligibility_fixture_db(db_path)
            result = build_eligibility_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                explicit_tickers=['MISSING.OL'],
            )
            self.assertEqual(result.rejection_counts_by_reason['missing_market_data'], 1)
            self.assertEqual(result.results[0].rejection_reasons, ('missing_market_data', 'insufficient_history_lt_min_rows'))

    def test_insufficient_history_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_eligibility_fixture_db(db_path)
            result = build_eligibility_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                explicit_tickers=['SHORT.OL'],
            )
            self.assertEqual(result.rejection_counts_by_reason['insufficient_history_lt_min_rows'], 1)

    def test_stale_latest_date_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_eligibility_fixture_db(db_path)
            result = build_eligibility_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                explicit_tickers=['STALE.OL'],
            )
            self.assertEqual(result.rejection_counts_by_reason['stale_latest_price_date'], 1)

    def test_duplicate_rows_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_eligibility_fixture_db(db_path)
            result = build_eligibility_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                explicit_tickers=['DUPL.OL'],
            )
            self.assertEqual(result.rejection_counts_by_reason['duplicate_ticker_date_rows'], 1)

    def test_invalid_ohlc_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_eligibility_fixture_db(db_path)
            result = build_eligibility_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                explicit_tickers=['BADOHLC.OL'],
            )
            self.assertEqual(result.rejection_counts_by_reason['invalid_ohlc_rows'], 1)

    def test_missing_required_ohlcv_columns_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_missing_ohlcv_fixture_db(db_path)
            result = build_eligibility_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
            )
            self.assertEqual(result.rejection_counts_by_reason['missing_required_ohlcv_columns'], 1)

    def test_missing_named_universe_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_eligibility_fixture_db(db_path)
            with self.assertRaisesRegex(ValueError, 'was not found in universe_cache'):
                build_eligibility_diagnostics(
                    db_path=db_path,
                    universe_id='SWEDEN_V1',
                )

    def test_named_universe_uses_cache_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_eligibility_fixture_db(db_path)
            result = build_eligibility_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
            )
            self.assertEqual(result.universe_source, 'universe_cache:NORWAY_V2')

    def test_eligibility_cli_writes_only_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'eligibility_output'
            _build_eligibility_fixture_db(db_path)
            exit_code = eligibility_cli_main(
                [
                    '--db-path',
                    str(db_path),
                    '--universe-id',
                    'NORWAY_V2',
                    '--out-dir',
                    str(out_dir),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                sorted(path.name for path in out_dir.iterdir()),
                ['eligibility_results.csv', 'eligibility_summary.json', 'eligibility_summary.md'],
            )

    def test_summary_counts_match_per_ticker_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_eligibility_fixture_db(db_path)
            result = build_eligibility_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
            )
            eligible_rows = sum(1 for row in result.results if row.eligible)
            rejected_rows = sum(1 for row in result.results if not row.eligible)
            self.assertEqual(result.eligible_count, eligible_rows)
            self.assertEqual(result.rejected_count, rejected_rows)

    def test_no_ranking_trade_signal_or_candidate_type_is_emitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'eligibility_output'
            _build_eligibility_fixture_db(db_path)
            eligibility_cli_main(
                [
                    '--db-path',
                    str(db_path),
                    '--universe-id',
                    'NORWAY_V2',
                    '--out-dir',
                    str(out_dir),
                ]
            )
            summary = json.loads((out_dir / 'eligibility_summary.json').read_text(encoding='utf-8'))
            with (out_dir / 'eligibility_results.csv').open('r', encoding='utf-8', newline='') as handle:
                header = csv.DictReader(handle).fieldnames or []
            forbidden_fields = {'raw_rank', 'raw_score', 'trade_signal', 'candidate_type'}
            self.assertTrue(forbidden_fields.isdisjoint(summary.keys()))
            self.assertTrue(forbidden_fields.isdisjoint(header))

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tradetool.diagnostics.coverage import build_coverage_diagnostics
from tradetool.diagnostics.coverage_cli import main as coverage_cli_main


def _build_fixture_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            'CREATE TABLE price_history (ticker TEXT, date TEXT, close REAL, volume REAL)'
        )
        connection.execute(
            'CREATE TABLE universe_membership (ticker TEXT, universe_id TEXT)'
        )
        rows: list[tuple[str | None, str | None, float | None, float | None]] = []
        rows.extend(('AAA.OL', f'2025-01-{day:02d}', 100.0 + day, 1000.0) for day in range(1, 31))
        rows.extend(('AAA.OL', f'2025-02-{day:02d}', 130.0 + day, 1000.0) for day in range(1, 29))
        rows.extend(('AAA.OL', f'2025-03-{day:02d}', 158.0 + day, 1000.0) for day in range(1, 32))
        rows.extend(('AAA.OL', f'2025-04-{day:02d}', 189.0 + day, 1000.0) for day in range(1, 31))
        rows.extend(('AAA.OL', f'2025-05-{day:02d}', 219.0 + day, 1000.0) for day in range(1, 32))
        rows.extend(('AAA.OL', f'2025-06-{day:02d}', 250.0 + day, 1000.0) for day in range(1, 31))
        rows.extend(('AAA.OL', f'2025-07-{day:02d}', 280.0 + day, 1000.0) for day in range(1, 32))
        rows.extend(('AAA.OL', f'2025-08-{day:02d}', 311.0 + day, 1000.0) for day in range(1, 32))
        rows.extend(('AAA.OL', f'2025-09-{day:02d}', 342.0 + day, 1000.0) for day in range(1, 31))
        rows.extend(('BBB.OL', f'2025-01-{day:02d}', 50.0 + day, 500.0) for day in range(1, 21))
        rows.append(('AAA.OL', '2025-09-30', 999.0, 1000.0))
        rows.append(('CCC.OL', 'bad-date', 70.0, 300.0))
        rows.append(('', '2025-09-29', 80.0, 200.0))
        rows.append((None, '2025-09-28', 81.0, 200.0))
        rows.append(('DDD.OL', None, 90.0, 100.0))
        rows.append(('EEE.OL', '2025-09-30', None, 100.0))
        connection.executemany('INSERT INTO price_history VALUES (?, ?, ?, ?)', rows)
        connection.executemany(
            'INSERT INTO universe_membership VALUES (?, ?)',
            [('AAA.OL', 'NORWAY_V2'), ('BBB.OL', 'NORWAY_V2'), ('ZZZ.OL', 'NORWAY_V2')],
        )


class CoverageDiagnosticsTests(unittest.TestCase):
    def test_schema_inspection_lists_tables_and_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_fixture_database(db_path)
            result = build_coverage_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                explicit_tickers=['AAA.OL', 'BBB.OL', 'ZZZ.OL'],
            )
            self.assertIn('price_history', result.schema.tables)
            self.assertIn('universe_membership', result.schema.tables)
            self.assertIn('price_history', result.schema.price_history_candidates)
            columns = {column.name for column in result.schema.columns_by_table['price_history']}
            self.assertIn('ticker', columns)
            self.assertIn('date', columns)

    def test_coverage_detects_counts_dates_distribution_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_fixture_database(db_path)
            result = build_coverage_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                explicit_tickers=['AAA.OL', 'BBB.OL', 'ZZZ.OL', ''],
            )
            self.assertEqual(result.report.input_universe_count, 4)
            self.assertEqual(result.report.valid_ticker_count, 3)
            self.assertEqual(result.report.market_data_coverage_count, 2)
            self.assertEqual(result.report.enough_history_count, 1)
            self.assertEqual(result.report.feature_complete_count, 1)
            self.assertEqual(result.report.eligible_count, 1)
            self.assertEqual(result.report.ranked_count, 0)
            self.assertEqual(result.row_count, 298)
            self.assertEqual(result.min_price_date, '2025-01-01')
            self.assertEqual(result.max_price_date, '2025-09-30')
            self.assertEqual(dict(result.report.latest_data_date_distribution), {'2025-01-20': 1, '2025-09-30': 2})
            self.assertEqual(result.duplicate_ticker_date_rows, 1)
            self.assertEqual(result.tickers_with_at_least_252_rows, 1)
            self.assertEqual(result.tickers_with_at_least_504_rows, 0)
            self.assertEqual(result.fresh_latest_data_count, 2)
            self.assertEqual(result.report.rejection_counts_by_reason['invalid_ticker'], 1)
            self.assertEqual(result.report.rejection_counts_by_reason['missing_market_data'], 1)
            self.assertEqual(result.report.rejection_counts_by_reason['insufficient_history_lt_252'], 1)
            self.assertEqual(result.report.rejection_counts_by_reason['duplicate_ticker_date_rows'], 1)
            self.assertIn('ZZZ.OL', result.report.missing_ticker_samples)

    def test_missing_price_table_is_reported_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            with sqlite3.connect(db_path) as connection:
                connection.execute('CREATE TABLE something_else (ticker TEXT)')
            with self.assertRaisesRegex(ValueError, 'price-history table'):
                build_coverage_diagnostics(
                    db_path=db_path,
                    universe_id='NORWAY_V2',
                    explicit_tickers=['AAA.OL'],
                )

    def test_cli_writes_expected_report_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'coverage_output'
            _build_fixture_database(db_path)
            exit_code = coverage_cli_main(
                [
                    '--db-path',
                    str(db_path),
                    '--universe-id',
                    'NORWAY_V2',
                    '--tickers',
                    'AAA.OL,BBB.OL,ZZZ.OL',
                    '--out-dir',
                    str(out_dir),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                sorted(path.name for path in out_dir.iterdir()),
                ['coverage_report.json', 'coverage_summary.md', 'latest_data_date_distribution.csv'],
            )
            payload = json.loads((out_dir / 'coverage_report.json').read_text(encoding='utf-8'))
            self.assertEqual(payload['coverage_report']['valid_ticker_count'], 3)

    def test_universe_csv_and_price_history_fallback_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            csv_path = Path(temp_dir) / 'universe.csv'
            _build_fixture_database(db_path)
            csv_path.write_text('ticker\nAAA.OL\nBBB.OL\n', encoding='utf-8')
            csv_result = build_coverage_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                universe_csv_path=csv_path,
            )
            fallback_result = build_coverage_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
            )
            self.assertEqual(csv_result.universe_source, f'csv:{csv_path.name}')
            self.assertEqual(fallback_result.universe_source, 'price_history_distinct_tickers')

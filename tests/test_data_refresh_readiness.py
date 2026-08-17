from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tradetool.diagnostics.data_refresh_readiness import (
    DECISION_BLOCKED_QUALITY,
    DECISION_BLOCKED_SCHEMA,
    DECISION_BLOCKED_UNSAFE,
    DECISION_READY,
    build_data_refresh_readiness_audit,
    write_data_refresh_readiness_outputs,
)
from tradetool.diagnostics.data_refresh_readiness_cli import main as data_refresh_readiness_cli_main
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import TRADE_POLICY_ENGINE_ID


def _create_base_schema(connection: sqlite3.Connection) -> None:
    connection.execute('CREATE TABLE price_history (ticker TEXT, price_date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, updated_at TEXT)')
    connection.execute('CREATE TABLE universe_cache (universe_key TEXT PRIMARY KEY, tickers_json TEXT, source_label TEXT, updated_at TEXT)')


def _insert_price(connection: sqlite3.Connection, ticker: str, price_date: str, open_value: float, high_value: float, low_value: float, close_value: float, volume_value: float) -> None:
    connection.execute(
        'INSERT INTO price_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (ticker, price_date, open_value, high_value, low_value, close_value, volume_value, f'{price_date}T16:00:00Z'),
    )


def _build_ready_fixture(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        _create_base_schema(connection)
        for day in range(1, 5):
            _insert_price(connection, 'AAA.OL', f'2026-06-1{day}', 100 + day, 101 + day, 99 + day, 100 + day, 1000)
            _insert_price(connection, 'BBB.OL', f'2026-06-1{day}', 200 + day, 201 + day, 199 + day, 200 + day, 2000)
            _insert_price(connection, '^OSEAX', f'2026-06-1{day}', 300 + day, 301 + day, 299 + day, 300 + day, 3000)
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('NORWAY_V2', json.dumps(['AAA.OL', 'BBB.OL']), 'fixture', '2026-08-17T00:00:00Z'),
        )


def _build_missing_column_fixture(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE price_history (ticker TEXT, close REAL, open REAL, high REAL, low REAL, volume REAL)')
        connection.execute('CREATE TABLE universe_cache (universe_key TEXT PRIMARY KEY, tickers_json TEXT, source_label TEXT, updated_at TEXT)')
        connection.execute(
            'INSERT INTO price_history VALUES (?, ?, ?, ?, ?, ?)',
            ('AAA.OL', 100.0, 100.0, 101.0, 99.0, 1000.0),
        )
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('NORWAY_V2', json.dumps(['AAA.OL']), 'fixture', '2026-08-17T00:00:00Z'),
        )


def _build_duplicate_fixture(path: Path) -> None:
    _build_ready_fixture(path)
    with sqlite3.connect(path) as connection:
        _insert_price(connection, 'AAA.OL', '2026-06-14', 104, 105, 103, 104, 1000)


def _build_invalid_ohlc_fixture(path: Path) -> None:
    _build_ready_fixture(path)
    with sqlite3.connect(path) as connection:
        _insert_price(connection, 'AAA.OL', '2026-06-20', 100, 99, 101, 100, 1000)


def _build_out_of_scope_invalid_ohlc_fixture(path: Path) -> None:
    _build_ready_fixture(path)
    with sqlite3.connect(path) as connection:
        _insert_price(connection, 'OUTSIDE.US', '2026-06-20', 100, 99, 101, 100, 1000)


class DataRefreshReadinessTests(unittest.TestCase):
    def test_cli_writes_exactly_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'audit_output'
            _build_ready_fixture(db_path)
            exit_code = data_refresh_readiness_cli_main(
                ['--db-path', str(db_path), '--universe-id', 'NORWAY_V2', '--benchmark-ticker', '^OSEAX', '--out-dir', str(out_dir)]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                sorted(path.name for path in out_dir.iterdir()),
                [
                    'data_refresh_readiness_summary.json',
                    'data_refresh_readiness_summary.md',
                    'price_date_coverage.csv',
                    'refresh_gap_by_ticker.csv',
                    'schema_write_risk_audit.csv',
                ],
            )

    def test_original_legacy_db_path_is_rejected_or_flagged_unsafe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_ready_fixture(db_path)
            result = build_data_refresh_readiness_audit(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
                unsafe_legacy_db_path=db_path,
                today=date(2026, 8, 17),
            )
            self.assertTrue(result.unsafe_legacy_db_path)
            self.assertEqual(result.recommendation, DECISION_BLOCKED_UNSAFE)

    def test_copied_db_path_can_be_audited_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_ready_fixture(db_path)
            result = build_data_refresh_readiness_audit(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
                today=date(2026, 8, 17),
            )
            self.assertTrue(result.read_only_open_status)
            self.assertEqual(result.recommendation, DECISION_READY)

    def test_required_price_columns_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_ready_fixture(db_path)
            result = build_data_refresh_readiness_audit(db_path=db_path, universe_id='NORWAY_V2', price_table='price_history')
            self.assertEqual(result.required_columns_missing, ())
            self.assertEqual(result.price_date_column, 'price_date')
            self.assertIn('open', result.required_columns_present)
            self.assertIn('volume', result.required_columns_present)

    def test_summary_reports_selected_date_column(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'audit_output'
            _build_ready_fixture(db_path)
            result = build_data_refresh_readiness_audit(db_path=db_path, universe_id='NORWAY_V2')
            write_data_refresh_readiness_outputs(result=result, out_dir=out_dir)
            summary = json.loads((out_dir / 'data_refresh_readiness_summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['price_date_column'], 'price_date')

    def test_missing_both_date_and_price_date_causes_blocked_schema_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_missing_column_fixture(db_path)
            result = build_data_refresh_readiness_audit(db_path=db_path, universe_id='NORWAY_V2', price_table='price_history')
            self.assertIn('date', result.required_columns_missing)
            self.assertEqual(result.recommendation, DECISION_BLOCKED_SCHEMA)

    def test_duplicate_ticker_date_rows_cause_blocked_data_quality_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_duplicate_fixture(db_path)
            result = build_data_refresh_readiness_audit(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            self.assertGreater(result.duplicate_ticker_date_rows, 0)
            self.assertEqual(result.recommendation, DECISION_BLOCKED_QUALITY)

    def test_invalid_ohlc_rows_cause_blocked_data_quality_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_invalid_ohlc_fixture(db_path)
            result = build_data_refresh_readiness_audit(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            self.assertGreater(result.invalid_ohlc_row_count, 0)
            self.assertEqual(result.recommendation, DECISION_BLOCKED_QUALITY)

    def test_invalid_ohlc_rows_outside_requested_universe_do_not_block_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_out_of_scope_invalid_ohlc_fixture(db_path)
            result = build_data_refresh_readiness_audit(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            self.assertEqual(result.invalid_ohlc_row_count, 0)
            self.assertEqual(result.recommendation, DECISION_READY)

    def test_universe_cache_source_is_used_for_named_universe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_ready_fixture(db_path)
            result = build_data_refresh_readiness_audit(db_path=db_path, universe_id='NORWAY_V2')
            self.assertEqual(result.detected_universe_source, 'universe_cache:NORWAY_V2')

    def test_benchmark_alignment_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_ready_fixture(db_path)
            result = build_data_refresh_readiness_audit(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            self.assertEqual(result.benchmark_latest_date, '2026-06-14')
            self.assertTrue(result.benchmark_aligned_with_universe_max)

    def test_refresh_gap_rows_are_produced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'audit_output'
            _build_ready_fixture(db_path)
            result = build_data_refresh_readiness_audit(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
                today=date(2026, 8, 17),
            )
            write_data_refresh_readiness_outputs(result=result, out_dir=out_dir)
            with (out_dir / 'refresh_gap_by_ticker.csv').open('r', encoding='utf-8', newline='') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertIn('days_behind_today', rows[0])

    def test_no_database_writes_occur(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_ready_fixture(db_path)
            with sqlite3.connect(db_path) as connection:
                before = connection.execute('SELECT COUNT(*) FROM price_history').fetchone()[0]
            build_data_refresh_readiness_audit(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            with sqlite3.connect(db_path) as connection:
                after = connection.execute('SELECT COUNT(*) FROM price_history').fetchone()[0]
            self.assertEqual(before, after)

    def test_no_ranking_feature_policy_candidate_type_ml_or_holdings_logic_is_changed(self) -> None:
        ranking_source = Path('src/tradetool/ranking/baseline.py').read_text(encoding='utf-8')
        candidate_source = Path('src/tradetool/policy/candidate_type.py').read_text(encoding='utf-8')
        trade_policy_source = Path('src/tradetool/policy/trade_policy.py').read_text(encoding='utf-8')
        audit_source = Path('src/tradetool/diagnostics/data_refresh_readiness.py').read_text(encoding='utf-8').lower()
        self.assertIn("BASELINE_RANKING_ENGINE_ID = 'baseline_v0_price_volume_rs'", ranking_source)
        self.assertIn("CANDIDATE_TYPE_ENGINE_ID = 'candidate_type_v0_diagnostic'", candidate_source)
        self.assertIn("TRADE_POLICY_ENGINE_ID = 'trade_policy_v1_balanced_diagnostic'", trade_policy_source)
        self.assertNotIn('holdings_signal', audit_source)
        self.assertNotIn('ml_score', audit_source)

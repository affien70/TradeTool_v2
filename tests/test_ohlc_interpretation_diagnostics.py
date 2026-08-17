from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.diagnostics.ohlc_interpretation import (
    build_ohlc_interpretation_audit,
    write_ohlc_interpretation_outputs,
)
from tradetool.diagnostics.ohlc_interpretation_cli import main as ohlc_interpretation_cli_main
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


def _build_adjustment_mismatch_fixture(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        _create_base_schema(connection)
        _insert_series(
            connection,
            ticker='AAA.OL',
            start_date=date(2025, 1, 1),
            row_count=260,
            invalid_offsets={
                250: {'close_value': 351.5},
                255: {'close_value': 356.5},
                259: {'close_value': 360.5},
            },
        )
        _insert_series(
            connection,
            ticker='BBB.OL',
            start_date=date(2025, 1, 1),
            row_count=260,
            invalid_offsets={
                10: {'close_value': 108.0},  # below low
                258: {'close_value': 359.5},
            },
        )
        _insert_series(connection, ticker='CCC.OL', start_date=date(2025, 1, 1), row_count=260)
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('NORWAY_V2', json.dumps(['AAA.OL', 'BBB.OL', 'CCC.OL']), 'fixture', '2026-08-17T00:00:00Z'),
        )


def _build_bad_raw_data_fixture(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        _create_base_schema(connection)
        _insert_series(
            connection,
            ticker='AAA.OL',
            start_date=date(2025, 1, 1),
            row_count=260,
            invalid_offsets={
                5: {'open_value': None},
                6: {'open_value': -1.0},
                7: {'high_value': 105.0, 'low_value': 106.0},
                8: {'open_value': 120.0},
            },
        )
        _insert_series(connection, ticker='BBB.OL', start_date=date(2025, 1, 1), row_count=260)
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('NORWAY_V2', json.dumps(['AAA.OL', 'BBB.OL']), 'fixture', '2026-08-17T00:00:00Z'),
        )


class OhlcInterpretationDiagnosticsTests(unittest.TestCase):
    def test_cli_writes_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'ohlc_output'
            _build_adjustment_mismatch_fixture(db_path)
            exit_code = ohlc_interpretation_cli_main(['--db-path', str(db_path), '--universe-id', 'NORWAY_V2', '--out-dir', str(out_dir)])
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                sorted(path.name for path in out_dir.iterdir()),
                [
                    'ohlc_by_date_interpretation.csv',
                    'ohlc_by_ticker_interpretation.csv',
                    'ohlc_interpretation_summary.json',
                    'ohlc_interpretation_summary.md',
                    'ohlc_ratio_samples.csv',
                    'ohlc_violation_patterns.csv',
                ],
            )

    def test_likely_adjusted_close_mismatch_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_adjustment_mismatch_fixture(db_path)
            result = build_ohlc_interpretation_audit(db_path=db_path, universe_id='NORWAY_V2')
            self.assertEqual(result.recommendation, 'likely_source_adjustment_mismatch')
            self.assertEqual(result.dominant_violation_pattern, 'close_above_high')
            self.assertEqual(result.latest_252_invalid_row_count, 5)
            self.assertGreater(result.ratio_statistics['close_over_high']['median'], 1.0)

    def test_bad_raw_data_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_bad_raw_data_fixture(db_path)
            result = build_ohlc_interpretation_audit(db_path=db_path, universe_id='NORWAY_V2')
            self.assertEqual(result.recommendation, 'likely_bad_raw_data')
            pattern_counts = {row.pattern: row.invalid_row_count for row in result.violation_pattern_rows}
            self.assertIn('missing_or_nonpositive_price', pattern_counts)
            self.assertIn('high_low_inverted', pattern_counts)

    def test_summary_and_csv_outputs_include_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'ohlc_output'
            _build_adjustment_mismatch_fixture(db_path)
            result = build_ohlc_interpretation_audit(db_path=db_path, universe_id='NORWAY_V2')
            write_ohlc_interpretation_outputs(result=result, out_dir=out_dir)
            summary = json.loads((out_dir / 'ohlc_interpretation_summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['price_date_column'], 'price_date')
            self.assertIn('ratio_statistics', summary)
            self.assertIn('refresh_design_implications', summary)
            with (out_dir / 'ohlc_ratio_samples.csv').open('r', encoding='utf-8', newline='') as handle:
                row = next(csv.DictReader(handle))
            self.assertIn('violation_pattern', row)
            self.assertIn('likely_interpretation', row)

    def test_no_database_writes_occur(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_adjustment_mismatch_fixture(db_path)
            with sqlite3.connect(db_path) as connection:
                before = connection.execute('SELECT COUNT(*) FROM price_history').fetchone()[0]
            build_ohlc_interpretation_audit(db_path=db_path, universe_id='NORWAY_V2')
            with sqlite3.connect(db_path) as connection:
                after = connection.execute('SELECT COUNT(*) FROM price_history').fetchone()[0]
            self.assertEqual(before, after)

    def test_no_ranking_feature_policy_candidate_type_ml_or_holdings_logic_is_changed(self) -> None:
        ranking_source = Path('src/tradetool/ranking/baseline.py').read_text(encoding='utf-8')
        candidate_source = Path('src/tradetool/policy/candidate_type.py').read_text(encoding='utf-8')
        trade_policy_source = Path('src/tradetool/policy/trade_policy.py').read_text(encoding='utf-8')
        interpretation_source = Path('src/tradetool/diagnostics/ohlc_interpretation.py').read_text(encoding='utf-8').lower()
        self.assertIn("BASELINE_RANKING_ENGINE_ID = 'baseline_v0_price_volume_rs'", ranking_source)
        self.assertIn("CANDIDATE_TYPE_ENGINE_ID = 'candidate_type_v0_diagnostic'", candidate_source)
        self.assertIn("TRADE_POLICY_ENGINE_ID = 'trade_policy_v1_balanced_diagnostic'", trade_policy_source)
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertNotIn('holdings_signal', interpretation_source)
        self.assertNotIn('ml_score', interpretation_source)

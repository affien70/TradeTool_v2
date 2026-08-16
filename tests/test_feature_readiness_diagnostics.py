from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.diagnostics.feature_readiness import build_feature_readiness_diagnostics
from tradetool.diagnostics.feature_readiness_cli import main as feature_readiness_cli_main
from tradetool.features import compute_raw_features
from tradetool.data import PriceHistoryRecord


def _series(ticker: str, closes: list[float], *, start: date, volume: float = 100.0) -> list[PriceHistoryRecord]:
    rows: list[PriceHistoryRecord] = []
    for index, close_value in enumerate(closes):
        rows.append(
            PriceHistoryRecord(
                ticker=ticker,
                price_date=start + timedelta(days=index),
                open=close_value,
                high=close_value + 1.0,
                low=close_value - 1.0,
                close=close_value,
                volume=volume + index,
            )
        )
    return rows


def _insert_rows(connection: sqlite3.Connection, ticker: str, closes: list[float], *, last_date: date, volume: float = 100.0) -> None:
    rows = []
    start = last_date - timedelta(days=len(closes) - 1)
    for index, close_value in enumerate(closes):
        current_date = start + timedelta(days=index)
        rows.append((ticker, current_date.isoformat(), close_value, close_value + 1.0, close_value - 1.0, close_value, volume + index))
    connection.executemany('INSERT INTO price_history VALUES (?, ?, ?, ?, ?, ?, ?)', rows)


def _build_feature_fixture_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            'CREATE TABLE price_history (ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)'
        )
        connection.execute(
            'CREATE TABLE universe_cache (universe_key TEXT PRIMARY KEY, tickers_json TEXT, source_label TEXT, updated_at TEXT)'
        )
        last_date = date(2025, 1, 1) + timedelta(days=259)
        good1 = [100.0 + index for index in range(260)]
        good2 = [200.0 + index * 0.5 for index in range(255)]
        short = [50.0 + index for index in range(200)]
        almost = [80.0 + index for index in range(200)]
        benchmark = [300.0 + index * 0.8 for index in range(260)]
        _insert_rows(connection, 'GOOD1.OL', good1, last_date=last_date, volume=1000.0)
        _insert_rows(connection, 'GOOD2.OL', good2, last_date=last_date, volume=800.0)
        _insert_rows(connection, 'SHORT.OL', short, last_date=last_date, volume=500.0)
        _insert_rows(connection, 'ALMOST.OL', almost, last_date=last_date, volume=400.0)
        _insert_rows(connection, '^OSEAX', benchmark, last_date=last_date, volume=2000.0)
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            (
                'NORWAY_V2',
                json.dumps(['GOOD1.OL', 'GOOD2.OL', 'SHORT.OL']),
                'fixture',
                '2026-08-16T00:00:00Z',
            ),
        )
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            (
                'ALT_V2',
                json.dumps(['GOOD1.OL', 'ALMOST.OL']),
                'fixture',
                '2026-08-16T00:00:00Z',
            ),
        )


class FeatureCalculationTests(unittest.TestCase):
    def test_feature_functions_compute_returns_correctly(self) -> None:
        closes = [100.0 + index for index in range(260)]
        result = compute_raw_features(_series('AAA.OL', closes, start=date(2025, 1, 1)))
        self.assertAlmostEqual(result.features['return_1m'], (359.0 / 339.0) - 1.0)
        self.assertAlmostEqual(result.features['return_3m'], (359.0 / 297.0) - 1.0)
        self.assertAlmostEqual(result.features['return_6m'], (359.0 / 234.0) - 1.0)
        self.assertAlmostEqual(result.features['return_12m'], (359.0 / 108.0) - 1.0)

    def test_sma_drawdown_volatility_and_liquidity_metrics_are_deterministic(self) -> None:
        closes = [100.0 + index for index in range(260)]
        result = compute_raw_features(_series('AAA.OL', closes, start=date(2025, 1, 1), volume=100.0))
        self.assertAlmostEqual(result.features['sma50'], sum(closes[-50:]) / 50.0)
        self.assertAlmostEqual(result.features['sma100'], sum(closes[-100:]) / 100.0)
        self.assertAlmostEqual(result.features['sma200'], sum(closes[-200:]) / 200.0)
        self.assertAlmostEqual(result.features['drawdown_252'], (108.0 / 359.0) - 1.0)
        self.assertGreater(result.features['volatility_63'], 0.0)
        self.assertGreater(result.features['volatility_126'], 0.0)
        self.assertAlmostEqual(result.features['average_volume_20'], sum(100.0 + index for index in range(240, 260)) / 20.0)
        traded_values = [(100.0 + index) * (100.0 + index) for index in range(260)]
        self.assertAlmostEqual(result.features['average_traded_value_20'], sum(traded_values[-20:]) / 20.0)

    def test_relative_strength_is_stock_return_minus_benchmark_return(self) -> None:
        stock = [100.0 + index for index in range(260)]
        benchmark = [200.0 + (index * 0.5) for index in range(260)]
        result = compute_raw_features(
            _series('AAA.OL', stock, start=date(2025, 1, 1)),
            benchmark_rows=_series('^OSEAX', benchmark, start=date(2025, 1, 1)),
            benchmark_ticker='^OSEAX',
        )
        self.assertAlmostEqual(
            result.features['relative_strength_1m'],
            result.features['return_1m'] - result.features['benchmark_return_1m'],
        )
        self.assertAlmostEqual(
            result.features['relative_strength_12m'],
            result.features['return_12m'] - result.features['benchmark_return_12m'],
        )


class FeatureReadinessDiagnosticsTests(unittest.TestCase):
    def test_feature_readiness_uses_only_structurally_eligible_tickers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_feature_fixture_db(db_path)
            result = build_feature_readiness_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
            )
            self.assertEqual(result.structural_eligible_count, 2)
            self.assertEqual(result.structural_rejected_count, 1)
            self.assertEqual(sorted(row.ticker for row in result.rows), ['GOOD1.OL', 'GOOD2.OL'])

    def test_structurally_rejected_tickers_are_counted_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_feature_fixture_db(db_path)
            result = build_feature_readiness_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
            )
            self.assertEqual(result.input_universe_count, 3)
            self.assertEqual(result.structural_eligible_count + result.structural_rejected_count, 3)

    def test_missing_benchmark_fails_clearly_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_feature_fixture_db(db_path)
            with self.assertRaisesRegex(ValueError, 'Benchmark ticker "\\^MISSING" is missing'):
                build_feature_readiness_diagnostics(
                    db_path=db_path,
                    universe_id='NORWAY_V2',
                    benchmark_ticker='^MISSING',
                )

    def test_missing_252_row_history_produces_feature_incomplete_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_feature_fixture_db(db_path)
            result = build_feature_readiness_diagnostics(
                db_path=db_path,
                universe_id='ALT_V2',
                min_history_rows=100,
            )
            almost_row = next(row for row in result.rows if row.ticker == 'ALMOST.OL')
            self.assertFalse(almost_row.feature_complete)
            self.assertIn('insufficient_rows_for_12m_return', almost_row.feature_missing_reasons)

    def test_feature_cli_writes_only_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'feature_output'
            _build_feature_fixture_db(db_path)
            exit_code = feature_readiness_cli_main(
                [
                    '--db-path',
                    str(db_path),
                    '--universe-id',
                    'NORWAY_V2',
                    '--benchmark-ticker',
                    '^OSEAX',
                    '--out-dir',
                    str(out_dir),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                sorted(path.name for path in out_dir.iterdir()),
                ['feature_readiness.csv', 'feature_readiness_summary.json', 'feature_readiness_summary.md'],
            )

    def test_feature_summary_counts_match_per_ticker_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_feature_fixture_db(db_path)
            result = build_feature_readiness_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
            )
            complete_count = sum(1 for row in result.rows if row.feature_complete)
            self.assertEqual(result.feature_complete_count, complete_count)
            self.assertEqual(result.feature_incomplete_count, len(result.rows) - complete_count)

    def test_no_ranking_scoring_trade_signal_or_candidate_type_is_produced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'feature_output'
            _build_feature_fixture_db(db_path)
            feature_readiness_cli_main(
                [
                    '--db-path',
                    str(db_path),
                    '--universe-id',
                    'NORWAY_V2',
                    '--benchmark-ticker',
                    '^OSEAX',
                    '--out-dir',
                    str(out_dir),
                ]
            )
            summary = json.loads((out_dir / 'feature_readiness_summary.json').read_text(encoding='utf-8'))
            with (out_dir / 'feature_readiness.csv').open('r', encoding='utf-8', newline='') as handle:
                header = csv.DictReader(handle).fieldnames or []
            forbidden_fields = {'raw_rank', 'raw_score', 'trade_signal', 'candidate_type'}
            self.assertTrue(forbidden_fields.isdisjoint(summary.keys()))
            self.assertTrue(forbidden_fields.isdisjoint(header))

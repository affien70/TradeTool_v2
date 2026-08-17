from __future__ import annotations

import csv
import json
import sqlite3
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.data import ReadOnlySQLite, load_price_history_for_tickers, load_price_history_v2_for_tickers
from tradetool.data.market_data_schema import initialize_market_data_schema, write_market_data_rows_for_test
from tradetool.data.market_data_schema import MarketDataRow
from tradetool.diagnostics.market_data_v2_readiness import (
    build_market_data_v2_readiness,
    write_market_data_v2_readiness_outputs,
)
from tradetool.diagnostics.market_data_v2_readiness_cli import main as market_data_v2_readiness_cli_main
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import TRADE_POLICY_ENGINE_ID


def _sample_v2_row(*, ticker: str, price_date: str, raw_close: float, adjusted_close: float, data_source: str = 'yahoo') -> MarketDataRow:
    return MarketDataRow(
        ticker=ticker,
        price_date=price_date,
        raw_open=raw_close - 1.0,
        raw_high=raw_close + 1.0,
        raw_low=raw_close - 2.0,
        raw_close=raw_close,
        adjusted_close=adjusted_close,
        volume=1000.0,
        data_source=data_source,
        created_at_utc='2026-08-17T00:00:00Z',
        updated_at_utc='2026-08-17T00:00:00Z',
    )


def _seed_v2_rows(db_path: Path) -> None:
    initialize_market_data_schema(db_path)
    rows: list[MarketDataRow] = []
    start = date(2025, 1, 1)
    for index in range(260):
        day = (start + timedelta(days=index)).isoformat()
        rows.append(_sample_v2_row(ticker='CAMBI.OL', price_date=day, raw_close=100.0 + index, adjusted_close=80.0 + index))
        rows.append(_sample_v2_row(ticker='SNTIA.OL', price_date=day, raw_close=200.0 + index, adjusted_close=150.0 + index))
        rows.append(_sample_v2_row(ticker='^OSEAX', price_date=day, raw_close=300.0 + index, adjusted_close=260.0 + index))
    for index in range(200):
        day = (start + timedelta(days=index)).isoformat()
        rows.append(_sample_v2_row(ticker='GOD.OL', price_date=day, raw_close=50.0 + index, adjusted_close=55.0 + index))
    rows.append(_sample_v2_row(ticker='CAMBI.OL', price_date='2025-12-31', raw_close=999.0, adjusted_close=888.0, data_source='alt'))
    write_market_data_rows_for_test(db_path=db_path, rows=rows, allow_test_db_write=True)


class MarketDataV2ReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_root = Path('/tmp')

    def _db_path(self, name: str) -> Path:
        path = self.temp_root / name
        if path.exists():
            path.unlink()
        return path

    def test_price_history_v2_reader_loads_only_requested_tickers(self) -> None:
        db_path = self._db_path('tradetool_v2_readiness_requested.sqlite')
        _seed_v2_rows(db_path)
        result = load_price_history_v2_for_tickers(db_path=str(db_path), tickers=['CAMBI.OL', 'GOD.OL'])
        self.assertEqual(sorted(result.rows_by_ticker), ['CAMBI.OL', 'GOD.OL'])
        self.assertNotIn('SNTIA.OL', result.rows_by_ticker)
        db_path.unlink()

    def test_reader_filters_by_data_source(self) -> None:
        db_path = self._db_path('tradetool_v2_readiness_datasource.sqlite')
        _seed_v2_rows(db_path)
        result = load_price_history_v2_for_tickers(db_path=str(db_path), tickers=['CAMBI.OL'], data_source='alt')
        self.assertEqual(len(result.rows_by_ticker['CAMBI.OL']), 1)
        self.assertEqual(result.rows_by_ticker['CAMBI.OL'][0].adjusted_close, 888.0)
        db_path.unlink()

    def test_reader_maps_adjusted_close_to_feature_close(self) -> None:
        db_path = self._db_path('tradetool_v2_readiness_closemap.sqlite')
        _seed_v2_rows(db_path)
        result = load_price_history_v2_for_tickers(db_path=str(db_path), tickers=['CAMBI.OL'])
        first_row = result.rows_by_ticker['CAMBI.OL'][0]
        self.assertEqual(first_row.close, first_row.adjusted_close)
        db_path.unlink()

    def test_reader_preserves_raw_close_separately(self) -> None:
        db_path = self._db_path('tradetool_v2_readiness_rawclose.sqlite')
        _seed_v2_rows(db_path)
        result = load_price_history_v2_for_tickers(db_path=str(db_path), tickers=['CAMBI.OL'])
        first_row = result.rows_by_ticker['CAMBI.OL'][0]
        self.assertNotEqual(first_row.raw_close, first_row.adjusted_close)
        db_path.unlink()

    def test_adjusted_close_outside_raw_high_low_is_accepted_by_read_path(self) -> None:
        db_path = self._db_path('tradetool_v2_readiness_adjusted.sqlite')
        initialize_market_data_schema(db_path)
        row = MarketDataRow(
            ticker='CAMBI.OL',
            price_date='2025-01-01',
            raw_open=100.0,
            raw_high=101.0,
            raw_low=99.0,
            raw_close=100.0,
            adjusted_close=80.0,
            volume=1000.0,
            data_source='yahoo',
            created_at_utc='2026-08-17T00:00:00Z',
            updated_at_utc='2026-08-17T00:00:00Z',
        )
        write_market_data_rows_for_test(db_path=db_path, rows=[row], allow_test_db_write=True)
        result = load_price_history_v2_for_tickers(db_path=str(db_path), tickers=['CAMBI.OL'])
        self.assertEqual(result.rows_by_ticker['CAMBI.OL'][0].close, 80.0)
        db_path.unlink()

    def test_feature_calculation_uses_adjusted_close_not_raw_close(self) -> None:
        db_path = self._db_path('tradetool_v2_readiness_featureclose.sqlite')
        _seed_v2_rows(db_path)
        result = build_market_data_v2_readiness(
            db_path=db_path,
            tickers=['CAMBI.OL'],
            benchmark_ticker='^OSEAX',
            data_source='yahoo',
        )
        row = result.feature_rows[0]
        self.assertEqual(row.latest_close, row.adjusted_close_latest)
        self.assertNotEqual(row.latest_close, row.raw_close_latest)
        db_path.unlink()

    def test_insufficient_v2_rows_are_reported_clearly(self) -> None:
        db_path = self._db_path('tradetool_v2_readiness_short.sqlite')
        _seed_v2_rows(db_path)
        result = build_market_data_v2_readiness(
            db_path=db_path,
            tickers=['GOD.OL'],
            benchmark_ticker='^OSEAX',
            data_source='yahoo',
        )
        self.assertEqual(result.insufficient_history_count, 1)
        self.assertIn('insufficient_rows_for_12m_return', result.missing_reason_counts)
        db_path.unlink()

    def test_benchmark_missing_is_reported_clearly(self) -> None:
        db_path = self._db_path('tradetool_v2_readiness_missing_bm.sqlite')
        _seed_v2_rows(db_path)
        with self.assertRaisesRegex(ValueError, 'Benchmark ticker "\\^MISSING" is missing from price_history_v2'):
            build_market_data_v2_readiness(
                db_path=db_path,
                tickers=['CAMBI.OL'],
                benchmark_ticker='^MISSING',
                data_source='yahoo',
            )
        db_path.unlink()

    def test_cli_writes_only_expected_report_files(self) -> None:
        db_path = self._db_path('tradetool_v2_readiness_cli.sqlite')
        out_dir = self._db_path('tradetool_v2_readiness_output')
        if out_dir.exists():
            for child in out_dir.iterdir():
                child.unlink()
            out_dir.rmdir()
        _seed_v2_rows(db_path)
        exit_code = market_data_v2_readiness_cli_main([
            '--db-path', str(db_path),
            '--tickers', 'CAMBI.OL', 'SNTIA.OL',
            '--benchmark-ticker', '^OSEAX',
            '--data-source', 'yahoo',
            '--out-dir', str(out_dir),
        ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            sorted(path.name for path in out_dir.iterdir()),
            [
                'market_data_v2_readiness_summary.json',
                'market_data_v2_readiness_summary.md',
                'v2_feature_readiness_sample.csv',
                'v2_price_coverage.csv',
            ],
        )
        db_path.unlink()
        for child in out_dir.iterdir():
            child.unlink()
        out_dir.rmdir()

    def test_no_db_writes_occur(self) -> None:
        db_path = self._db_path('tradetool_v2_readiness_nowrite.sqlite')
        _seed_v2_rows(db_path)
        before = initialize_market_data_schema(db_path).row_count
        build_market_data_v2_readiness(
            db_path=db_path,
            tickers=['CAMBI.OL'],
            benchmark_ticker='^OSEAX',
            data_source='yahoo',
        )
        after = initialize_market_data_schema(db_path).row_count
        self.assertEqual(before, after)
        db_path.unlink()

    def test_legacy_price_history_read_path_is_unchanged(self) -> None:
        db_path = self._db_path('tradetool_v2_readiness_legacy.sqlite')
        with sqlite3.connect(db_path) as connection:
            connection.execute('CREATE TABLE price_history (ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)')
            connection.execute("INSERT INTO price_history VALUES ('AAA.OL', '2025-01-01', 1, 2, 0.5, 1.5, 10)")
        loaded = load_price_history_for_tickers(database=ReadOnlySQLite(db_path), tickers=['AAA.OL'])
        self.assertEqual(loaded.rows_by_ticker['AAA.OL'][0].close, 1.5)
        db_path.unlink()

    def test_no_ranking_feature_formula_policy_candidate_type_ml_or_holdings_logic_is_changed(self) -> None:
        feature_source = Path('src/tradetool/features/raw.py').read_text(encoding='utf-8')
        ranking_source = Path('src/tradetool/ranking/baseline.py').read_text(encoding='utf-8')
        candidate_source = Path('src/tradetool/policy/candidate_type.py').read_text(encoding='utf-8')
        trade_policy_source = Path('src/tradetool/policy/trade_policy.py').read_text(encoding='utf-8')
        readiness_source = Path('src/tradetool/diagnostics/market_data_v2_readiness.py').read_text(encoding='utf-8').lower()
        self.assertIn('return (latest_value / base_value) - 1.0', feature_source)
        self.assertIn("BASELINE_RANKING_ENGINE_ID = 'baseline_v0_price_volume_rs'", ranking_source)
        self.assertIn("CANDIDATE_TYPE_ENGINE_ID = 'candidate_type_v0_diagnostic'", candidate_source)
        self.assertIn("TRADE_POLICY_ENGINE_ID = 'trade_policy_v1_balanced_diagnostic'", trade_policy_source)
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertNotIn('trade_signal', readiness_source)
        self.assertNotIn('holdings_signal', readiness_source)

    def test_summary_outputs_include_adjusted_close_mapping(self) -> None:
        db_path = self._db_path('tradetool_v2_readiness_summary.sqlite')
        out_dir = self._db_path('tradetool_v2_readiness_summary_output')
        if out_dir.exists():
            for child in out_dir.iterdir():
                child.unlink()
            out_dir.rmdir()
        _seed_v2_rows(db_path)
        result = build_market_data_v2_readiness(
            db_path=db_path,
            tickers=['CAMBI.OL'],
            benchmark_ticker='^OSEAX',
            data_source='yahoo',
        )
        write_market_data_v2_readiness_outputs(result=result, out_dir=out_dir)
        summary = json.loads((out_dir / 'market_data_v2_readiness_summary.json').read_text(encoding='utf-8'))
        with (out_dir / 'v2_feature_readiness_sample.csv').open('r', encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(summary['feature_complete_count'], 1)
        self.assertEqual(rows[0]['close_input_source'], 'adjusted_close')
        db_path.unlink()
        for child in out_dir.iterdir():
            child.unlink()
        out_dir.rmdir()

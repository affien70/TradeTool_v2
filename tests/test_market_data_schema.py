from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tradetool.data.market_data_schema import (
    LEGACY_PRODUCTION_DB_PATH,
    MarketDataRow,
    RAW_CLOSE_BOUNDARY_ABSOLUTE_TOLERANCE,
    RAW_CLOSE_BOUNDARY_RELATIVE_TOLERANCE,
    V2_PRICE_TABLE_NAME,
    _apply_market_data_rows_for_test,
    dry_run_market_data_rows,
    initialize_market_data_schema,
    inspect_market_data_schema,
    validate_market_data_db_path,
)
from tradetool.diagnostics.market_data_schema import build_market_data_schema_report, write_market_data_schema_report
from tradetool.diagnostics.market_data_schema_cli import main as market_data_schema_cli_main
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import TRADE_POLICY_ENGINE_ID


def _sample_row(**overrides: object) -> MarketDataRow:
    payload: dict[str, object] = {
        'ticker': 'AAA.OL',
        'price_date': '2026-08-14',
        'raw_open': 100.0,
        'raw_high': 105.0,
        'raw_low': 99.0,
        'raw_close': 104.0,
        'adjusted_close': 96.0,
        'volume': 1000.0,
        'data_source': 'fixture',
        'created_at_utc': '2026-08-17T00:00:00Z',
        'updated_at_utc': '2026-08-17T00:00:00Z',
    }
    payload.update(overrides)
    return MarketDataRow(**payload)


class MarketDataSchemaTests(unittest.TestCase):
    def test_schema_ddl_creates_price_history_v2(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_create.sqlite')
        if db_path.exists():
            db_path.unlink()
        inspection = initialize_market_data_schema(db_path)
        self.assertTrue(inspection.schema_initialized)
        self.assertEqual(inspection.table_name, V2_PRICE_TABLE_NAME)
        self.assertEqual(inspection.row_count, 0)
        db_path.unlink()

    def test_schema_has_raw_ohlc_and_adjusted_close_separate_columns(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_columns.sqlite')
        if db_path.exists():
            db_path.unlink()
        initialize_market_data_schema(db_path)
        inspection = inspect_market_data_schema(db_path)
        column_names = {column.name for column in inspection.columns}
        self.assertIn('raw_open', column_names)
        self.assertIn('raw_high', column_names)
        self.assertIn('raw_low', column_names)
        self.assertIn('raw_close', column_names)
        self.assertIn('adjusted_close', column_names)
        db_path.unlink()

    def test_primary_key_prevents_duplicate_ticker_date_source_rows(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_duplicate.sqlite')
        if db_path.exists():
            db_path.unlink()
        initialize_market_data_schema(db_path)
        row = _sample_row()
        _apply_market_data_rows_for_test(db_path=db_path, rows=[row])
        with self.assertRaises(sqlite3.IntegrityError):
            _apply_market_data_rows_for_test(db_path=db_path, rows=[row])
        db_path.unlink()

    def test_valid_synthetic_row_passes_dry_run_validation(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_valid.sqlite')
        if db_path.exists():
            db_path.unlink()
        result = dry_run_market_data_rows(db_path=db_path, rows=[_sample_row()])
        self.assertEqual(result.valid_row_count, 1)
        self.assertEqual(result.invalid_row_count, 0)
        self.assertEqual(result.would_insert, 1)

    def test_adjusted_close_outside_raw_ohlc_range_is_allowed(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_adjusted.sqlite')
        if db_path.exists():
            db_path.unlink()
        row = _sample_row(adjusted_close=90.0)
        result = dry_run_market_data_rows(db_path=db_path, rows=[row])
        self.assertEqual(result.invalid_row_count, 0)
        self.assertEqual(result.rows[0].action, 'would_insert')

    def test_raw_ohlc_inversion_is_rejected(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_inversion.sqlite')
        if db_path.exists():
            db_path.unlink()
        row = _sample_row(raw_high=98.0, raw_low=99.0)
        result = dry_run_market_data_rows(db_path=db_path, rows=[row])
        self.assertEqual(result.invalid_row_count, 1)
        self.assertIn('raw_high_lower_than_raw_low', result.invalid_reasons)

    def test_small_raw_low_higher_than_raw_close_is_tolerated_with_warning(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_tolerated_low_close.sqlite')
        if db_path.exists():
            db_path.unlink()
        row = _sample_row(raw_open=86.9, raw_high=87.0, raw_low=85.0999984741211, raw_close=85.0, adjusted_close=85.0)
        result = dry_run_market_data_rows(db_path=db_path, rows=[row])
        self.assertEqual(RAW_CLOSE_BOUNDARY_ABSOLUTE_TOLERANCE, 0.10)
        self.assertEqual(RAW_CLOSE_BOUNDARY_RELATIVE_TOLERANCE, 0.0025)
        self.assertEqual(result.invalid_row_count, 0)
        self.assertEqual(result.valid_row_count, 1)
        self.assertEqual(result.tolerated_warnings, {'raw_low_higher_than_raw_close_tolerated': 1})
        self.assertEqual(result.rows[0].validation_warnings, ('raw_low_higher_than_raw_close_tolerated',))
        initialize_market_data_schema(db_path)
        _apply_market_data_rows_for_test(db_path=db_path, rows=[row])
        self.assertEqual(inspect_market_data_schema(db_path).row_count, 1)
        db_path.unlink()

    def test_small_raw_high_lower_than_raw_close_is_tolerated_with_warning(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_tolerated_high_close.sqlite')
        if db_path.exists():
            db_path.unlink()
        row = _sample_row(raw_open=104.0, raw_high=104.95, raw_low=99.0, raw_close=105.0, adjusted_close=105.0)
        result = dry_run_market_data_rows(db_path=db_path, rows=[row])
        self.assertEqual(result.invalid_row_count, 0)
        self.assertEqual(result.tolerated_warnings, {'raw_high_lower_than_raw_close_tolerated': 1})
        initialize_market_data_schema(db_path)
        _apply_market_data_rows_for_test(db_path=db_path, rows=[row])
        self.assertEqual(inspect_market_data_schema(db_path).row_count, 1)
        db_path.unlink()

    def test_large_raw_low_higher_than_raw_close_still_fails(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_large_low_close.sqlite')
        if db_path.exists():
            db_path.unlink()
        row = _sample_row(raw_open=86.5, raw_high=87.0, raw_low=86.0, raw_close=85.0, adjusted_close=85.0)
        result = dry_run_market_data_rows(db_path=db_path, rows=[row])
        self.assertEqual(result.invalid_row_count, 1)
        self.assertEqual(result.tolerated_warnings, {})
        self.assertIn('raw_low_higher_than_raw_close', result.invalid_reasons)

    def test_large_raw_high_lower_than_raw_close_still_fails(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_large_high_close.sqlite')
        if db_path.exists():
            db_path.unlink()
        row = _sample_row(raw_open=103.0, raw_high=104.0, raw_low=99.0, raw_close=105.0, adjusted_close=105.0)
        result = dry_run_market_data_rows(db_path=db_path, rows=[row])
        self.assertEqual(result.invalid_row_count, 1)
        self.assertEqual(result.tolerated_warnings, {})
        self.assertIn('raw_high_lower_than_raw_close', result.invalid_reasons)

    def test_open_outside_raw_high_low_is_not_tolerated(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_open_outside.sqlite')
        if db_path.exists():
            db_path.unlink()
        row = _sample_row(raw_open=105.05, raw_high=105.0, raw_low=99.0, raw_close=104.0)
        result = dry_run_market_data_rows(db_path=db_path, rows=[row])
        self.assertEqual(result.invalid_row_count, 1)
        self.assertEqual(result.tolerated_warnings, {})
        self.assertIn('raw_high_lower_than_raw_open', result.invalid_reasons)

    def test_nonpositive_adjusted_close_is_rejected(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_nonpositive_adj.sqlite')
        if db_path.exists():
            db_path.unlink()
        row = _sample_row(adjusted_close=0.0)
        result = dry_run_market_data_rows(db_path=db_path, rows=[row])
        self.assertEqual(result.invalid_row_count, 1)
        self.assertIn('nonpositive_adjusted_close', result.invalid_reasons)

    def test_negative_volume_is_rejected(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_negative_volume.sqlite')
        if db_path.exists():
            db_path.unlink()
        row = _sample_row(volume=-1.0)
        result = dry_run_market_data_rows(db_path=db_path, rows=[row])
        self.assertEqual(result.invalid_row_count, 1)
        self.assertIn('negative_volume', result.invalid_reasons)

    def test_dry_run_writer_does_not_write_rows(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_dry_run.sqlite')
        if db_path.exists():
            db_path.unlink()
        initialize_market_data_schema(db_path)
        result = dry_run_market_data_rows(db_path=db_path, rows=[_sample_row()])
        inspection = inspect_market_data_schema(db_path)
        self.assertEqual(result.would_insert, 1)
        self.assertEqual(inspection.row_count, 0)
        db_path.unlink()

    def test_explicit_legacy_production_db_path_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            validate_market_data_db_path(LEGACY_PRODUCTION_DB_PATH)

    def test_cli_writes_only_expected_report_files(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_cli.sqlite')
        out_dir = Path('/tmp/tradetool_v2_schema_cli_output')
        if db_path.exists():
            db_path.unlink()
        if out_dir.exists():
            for child in out_dir.iterdir():
                child.unlink()
            out_dir.rmdir()
        exit_code = market_data_schema_cli_main(['--db-path', str(db_path), '--init-schema', '--out-dir', str(out_dir)])
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            sorted(path.name for path in out_dir.iterdir()),
            [
                'market_data_schema_columns.csv',
                'market_data_schema_summary.json',
                'market_data_schema_summary.md',
            ],
        )
        db_path.unlink()
        for child in out_dir.iterdir():
            child.unlink()
        out_dir.rmdir()

    def test_report_includes_no_db_write_and_next_safe_scope(self) -> None:
        db_path = Path('/tmp/tradetool_v2_schema_report.sqlite')
        out_dir = Path('/tmp/tradetool_v2_schema_report_output')
        if db_path.exists():
            db_path.unlink()
        if out_dir.exists():
            for child in out_dir.iterdir():
                child.unlink()
            out_dir.rmdir()
        report = build_market_data_schema_report(db_path=db_path, init_schema=True)
        write_market_data_schema_report(report=report, out_dir=out_dir)
        summary = json.loads((out_dir / 'market_data_schema_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['table_name'], V2_PRICE_TABLE_NAME)
        with (out_dir / 'market_data_schema_columns.csv').open('r', encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(any(row['name'] == 'adjusted_close' for row in rows))
        markdown = (out_dir / 'market_data_schema_summary.md').read_text(encoding='utf-8').lower()
        self.assertIn('no market rows are inserted', markdown)
        self.assertIn('no network fetch is performed', markdown)
        db_path.unlink()
        for child in out_dir.iterdir():
            child.unlink()
        out_dir.rmdir()

    def test_no_network_fetch_is_performed(self) -> None:
        schema_source = Path('src/tradetool/data/market_data_schema.py').read_text(encoding='utf-8').lower()
        cli_source = Path('src/tradetool/diagnostics/market_data_schema_cli.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('yfinance', schema_source)
        self.assertNotIn('requests', schema_source)
        self.assertNotIn('urllib', schema_source)
        self.assertNotIn('yfinance', cli_source)
        self.assertNotIn('requests', cli_source)
        self.assertNotIn('urllib', cli_source)

    def test_legacy_price_history_read_path_is_unchanged(self) -> None:
        readonly_source = Path('src/tradetool/data/sqlite_readonly.py').read_text(encoding='utf-8')
        self.assertIn("mode=ro", readonly_source)
        self.assertIn("PRICE_VALUE_HINTS = ('close', 'adj_close', 'adjusted_close', 'last')", readonly_source)

    def test_no_ranking_feature_policy_candidate_type_ml_or_holdings_logic_is_changed(self) -> None:
        ranking_source = Path('src/tradetool/ranking/baseline.py').read_text(encoding='utf-8')
        candidate_source = Path('src/tradetool/policy/candidate_type.py').read_text(encoding='utf-8')
        trade_policy_source = Path('src/tradetool/policy/trade_policy.py').read_text(encoding='utf-8')
        schema_source = Path('src/tradetool/data/market_data_schema.py').read_text(encoding='utf-8').lower()
        self.assertIn("BASELINE_RANKING_ENGINE_ID = 'baseline_v0_price_volume_rs'", ranking_source)
        self.assertIn("CANDIDATE_TYPE_ENGINE_ID = 'candidate_type_v0_diagnostic'", candidate_source)
        self.assertIn("TRADE_POLICY_ENGINE_ID = 'trade_policy_v1_balanced_diagnostic'", trade_policy_source)
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertNotIn('holdings_signal', schema_source)
        self.assertNotIn('ml_score', schema_source)

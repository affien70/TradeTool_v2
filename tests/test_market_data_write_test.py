from __future__ import annotations

import csv
import json
import sqlite3
import unittest
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from unittest.mock import patch

from tradetool.data.market_data_schema import (
    LEGACY_PRODUCTION_DB_PATH,
    V2_PRICE_TABLE_NAME,
    initialize_market_data_schema,
    inspect_market_data_schema,
    write_market_data_rows_for_test,
)
from tradetool.data.market_data_source import MarketDataSourceFetchResult, SourceMarketDataRow, TickerFetchStatus
from tradetool.diagnostics.market_data_write_test import (
    build_market_data_write_test_result,
    write_market_data_write_test_outputs,
)
from tradetool.diagnostics.market_data_write_test_cli import main as market_data_write_test_cli_main
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import TRADE_POLICY_ENGINE_ID


@dataclass
class _SyntheticSource:
    source_name: str
    result: MarketDataSourceFetchResult

    def fetch_daily_rows(self, *, tickers, start_date, end_date):
        return self.result


def _result_with_rows(rows: tuple[SourceMarketDataRow, ...], statuses: tuple[TickerFetchStatus, ...], provider_error: str | None = None):
    return MarketDataSourceFetchResult(
        source_name='synthetic',
        requested_tickers=tuple(status.ticker for status in statuses),
        rows=rows,
        ticker_statuses=statuses,
        provider_error=provider_error,
    )


def _sample_source_row(**overrides):
    payload = {
        'ticker': 'CAMBI.OL',
        'price_date': '2026-08-14',
        'raw_open': 100.0,
        'raw_high': 105.0,
        'raw_low': 99.0,
        'raw_close': 104.0,
        'adjusted_close': 96.0,
        'volume': 1000.0,
        'data_source': 'yahoo',
        'warning_codes': (),
    }
    payload.update(overrides)
    return SourceMarketDataRow(**payload)


class MarketDataWriteTestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path('/tmp/tradetool_v2_market_write_unit.sqlite')
        self.out_dir = Path('/tmp/tradetool_v2_market_write_unit_output')
        if self.db_path.exists():
            self.db_path.unlink()
        if self.out_dir.exists():
            for child in self.out_dir.iterdir():
                child.unlink()
            self.out_dir.rmdir()

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()
        if self.out_dir.exists():
            for child in self.out_dir.iterdir():
                child.unlink()
            self.out_dir.rmdir()

    def test_valid_rows_are_inserted_into_temporary_price_history_v2(self) -> None:
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(),),
                statuses=(TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),),
            ),
        )
        result = build_market_data_write_test_result(
            db_path=self.db_path,
            tickers=['CAMBI.OL'],
            start_date=date(2026, 6, 19),
            end_date=date(2026, 8, 17),
            source_name='synthetic',
            allow_test_db_write=True,
            source_override=source,
        )
        inspection = inspect_market_data_schema(self.db_path)
        self.assertEqual(result.write_result.inserted_count, 1)
        self.assertEqual(result.write_result.updated_count, 0)
        self.assertEqual(result.write_result.skipped_count, 0)
        self.assertEqual(inspection.row_count, 1)

    def test_repeated_write_is_idempotent_and_does_not_duplicate_rows(self) -> None:
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(),),
                statuses=(TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),),
            ),
        )
        build_market_data_write_test_result(
            db_path=self.db_path,
            tickers=['CAMBI.OL'],
            start_date=date(2026, 6, 19),
            end_date=date(2026, 8, 17),
            source_name='synthetic',
            allow_test_db_write=True,
            source_override=source,
        )
        second = build_market_data_write_test_result(
            db_path=self.db_path,
            tickers=['CAMBI.OL'],
            start_date=date(2026, 6, 19),
            end_date=date(2026, 8, 17),
            source_name='synthetic',
            allow_test_db_write=True,
            source_override=source,
        )
        inspection = inspect_market_data_schema(self.db_path)
        self.assertEqual(second.write_result.inserted_count, 0)
        self.assertEqual(second.write_result.updated_count, 0)
        self.assertEqual(second.write_result.skipped_count, 1)
        self.assertEqual(inspection.row_count, 1)

    def test_changed_existing_row_is_updated_deterministically(self) -> None:
        source_a = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(raw_close=104.0),),
                statuses=(TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),),
            ),
        )
        source_b = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(raw_high=111.0, raw_close=110.0, adjusted_close=102.0),),
                statuses=(TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),),
            ),
        )
        build_market_data_write_test_result(
            db_path=self.db_path,
            tickers=['CAMBI.OL'],
            start_date=date(2026, 6, 19),
            end_date=date(2026, 8, 17),
            source_name='synthetic',
            allow_test_db_write=True,
            source_override=source_a,
        )
        second = build_market_data_write_test_result(
            db_path=self.db_path,
            tickers=['CAMBI.OL'],
            start_date=date(2026, 6, 19),
            end_date=date(2026, 8, 17),
            source_name='synthetic',
            allow_test_db_write=True,
            source_override=source_b,
        )
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                f'SELECT raw_close, adjusted_close FROM {V2_PRICE_TABLE_NAME} WHERE ticker = ?',
                ('CAMBI.OL',),
            ).fetchone()
        self.assertEqual(second.write_result.updated_count, 1)
        self.assertEqual(row[0], 110.0)
        self.assertEqual(row[1], 102.0)

    def test_invalid_rows_are_rejected_and_not_written(self) -> None:
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(raw_high=98.0, raw_low=99.0),),
                statuses=(TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),),
            ),
        )
        result = build_market_data_write_test_result(
            db_path=self.db_path,
            tickers=['CAMBI.OL'],
            start_date=date(2026, 6, 19),
            end_date=date(2026, 8, 17),
            source_name='synthetic',
            allow_test_db_write=True,
            source_override=source,
        )
        inspection = inspect_market_data_schema(self.db_path)
        self.assertEqual(result.write_result.invalid_row_count, 1)
        self.assertIn('raw_high_lower_than_raw_low', result.write_result.invalid_reasons)
        self.assertEqual(inspection.row_count, 0)

    def test_adjusted_close_outside_raw_ohlc_range_is_allowed(self) -> None:
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(adjusted_close=90.0),),
                statuses=(TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),),
            ),
        )
        result = build_market_data_write_test_result(
            db_path=self.db_path,
            tickers=['CAMBI.OL'],
            start_date=date(2026, 6, 19),
            end_date=date(2026, 8, 17),
            source_name='synthetic',
            allow_test_db_write=True,
            source_override=source,
        )
        self.assertEqual(result.write_result.invalid_row_count, 0)
        self.assertEqual(result.write_result.inserted_count, 1)

    def test_legacy_production_db_path_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            write_market_data_rows_for_test(
                db_path=LEGACY_PRODUCTION_DB_PATH,
                rows=(),
                allow_test_db_write=True,
            )

    def test_write_requires_explicit_allow_flag(self) -> None:
        initialize_market_data_schema(self.db_path)
        with self.assertRaises(ValueError):
            write_market_data_rows_for_test(
                db_path=self.db_path,
                rows=(),
                allow_test_db_write=False,
            )
        with self.assertRaises(SystemExit):
            market_data_write_test_cli_main([
                '--db-path', str(self.db_path),
                '--tickers', 'CAMBI.OL',
                '--start-date', '2026-06-19',
                '--source', 'synthetic',
                '--out-dir', str(self.out_dir),
            ])

    def test_transaction_rollback_prevents_partial_writes_on_write_failure(self) -> None:
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(
                    _sample_source_row(ticker='CAMBI.OL'),
                    _sample_source_row(ticker='SNTIA.OL'),
                ),
                statuses=(
                    TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),
                    TickerFetchStatus('SNTIA.OL', True, 1, 'fetched'),
                ),
            ),
        )
        original = __import__('tradetool.data.market_data_schema', fromlist=['_execute_market_data_upsert'])._execute_market_data_upsert

        call_count = {'value': 0}

        def _raising_upsert(connection, row):
            call_count['value'] += 1
            if call_count['value'] == 2:
                raise sqlite3.DatabaseError('forced failure')
            return original(connection, row)

        with patch('tradetool.data.market_data_schema._execute_market_data_upsert', side_effect=_raising_upsert):
            with self.assertRaises(sqlite3.DatabaseError):
                build_market_data_write_test_result(
                    db_path=self.db_path,
                    tickers=['CAMBI.OL', 'SNTIA.OL'],
                    start_date=date(2026, 6, 19),
                    end_date=date(2026, 8, 17),
                    source_name='synthetic',
                    allow_test_db_write=True,
                    source_override=source,
                )
        inspection = inspect_market_data_schema(self.db_path)
        self.assertEqual(inspection.row_count, 0)

    def test_cli_writes_only_expected_report_files(self) -> None:
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(),),
                statuses=(TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),),
            ),
        )
        with patch('tradetool.diagnostics.market_data_write_test.build_market_data_source', return_value=source):
            exit_code = market_data_write_test_cli_main([
                '--db-path', str(self.db_path),
                '--tickers', 'CAMBI.OL',
                '--start-date', '2026-06-19',
                '--source', 'synthetic',
                '--allow-test-db-write',
                '--out-dir', str(self.out_dir),
            ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            sorted(path.name for path in self.out_dir.iterdir()),
            [
                'invalid_rows.csv',
                'market_data_write_test_summary.json',
                'market_data_write_test_summary.md',
                'ticker_fetch_status.csv',
                'written_rows_sample.csv',
            ],
        )

    def test_summary_outputs_include_written_row_counts(self) -> None:
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(),),
                statuses=(TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),),
            ),
        )
        result = build_market_data_write_test_result(
            db_path=self.db_path,
            tickers=['CAMBI.OL'],
            start_date=date(2026, 6, 19),
            end_date=date(2026, 8, 17),
            source_name='synthetic',
            allow_test_db_write=True,
            source_override=source,
        )
        write_market_data_write_test_outputs(result=result, out_dir=self.out_dir)
        summary = json.loads((self.out_dir / 'market_data_write_test_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['inserted_count'], 1)
        with (self.out_dir / 'written_rows_sample.csv').open('r', encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]['ticker'], 'CAMBI.OL')

    def test_no_network_is_required_for_unit_tests(self) -> None:
        source_module = Path('src/tradetool/data/market_data_source.py').read_text(encoding='utf-8').lower()
        write_module = Path('src/tradetool/diagnostics/market_data_write_test.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('requests.get', source_module)
        self.assertNotIn('urllib.request', source_module)
        self.assertNotIn('httpx.', source_module)
        self.assertNotIn('requests.get', write_module)
        self.assertNotIn('urllib.request', write_module)

    def test_no_legacy_price_history_modification_occurs(self) -> None:
        write_module = Path('src/tradetool/diagnostics/market_data_write_test.py').read_text(encoding='utf-8')
        schema_module = Path('src/tradetool/data/market_data_schema.py').read_text(encoding='utf-8')
        self.assertNotIn('INSERT INTO price_history ', write_module)
        self.assertNotIn('INSERT INTO price_history ', schema_module)
        self.assertIn('price_history_v2', schema_module)

    def test_no_ranking_feature_policy_candidate_type_ml_or_holdings_logic_is_changed(self) -> None:
        ranking_source = Path('src/tradetool/ranking/baseline.py').read_text(encoding='utf-8')
        candidate_source = Path('src/tradetool/policy/candidate_type.py').read_text(encoding='utf-8')
        trade_policy_source = Path('src/tradetool/policy/trade_policy.py').read_text(encoding='utf-8')
        write_source = Path('src/tradetool/diagnostics/market_data_write_test.py').read_text(encoding='utf-8').lower()
        self.assertIn("BASELINE_RANKING_ENGINE_ID = 'baseline_v0_price_volume_rs'", ranking_source)
        self.assertIn("CANDIDATE_TYPE_ENGINE_ID = 'candidate_type_v0_diagnostic'", candidate_source)
        self.assertIn("TRADE_POLICY_ENGINE_ID = 'trade_policy_v1_balanced_diagnostic'", trade_policy_source)
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertNotIn('holdings_signal', write_source)
        self.assertNotIn('ml_score', write_source)

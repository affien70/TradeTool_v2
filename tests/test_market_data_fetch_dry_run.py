from __future__ import annotations

import csv
import json
import sqlite3
import unittest
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from unittest.mock import patch

from tradetool.data.market_data_schema import LEGACY_PRODUCTION_DB_PATH, initialize_market_data_schema, inspect_market_data_schema
from tradetool.data.market_data_source import (
    MarketDataSourceDependencyError,
    MarketDataSourceFetchResult,
    SourceMarketDataRow,
    TickerFetchStatus,
    _normalize_yahoo_row,
)
from tradetool.diagnostics.market_data_fetch_dry_run import (
    build_market_data_fetch_dry_run,
    write_market_data_fetch_dry_run_outputs,
)
from tradetool.diagnostics.market_data_fetch_dry_run_cli import main as market_data_fetch_dry_run_cli_main
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


class MarketDataFetchDryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path('/tmp/tradetool_v2_market_fetch_unit.sqlite')
        self.out_dir = Path('/tmp/tradetool_v2_market_fetch_unit_output')
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

    def test_normalized_rows_keep_raw_ohlc_separate_from_adjusted_close(self) -> None:
        initialize_market_data_schema(self.db_path)
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(adjusted_close=94.0),),
                statuses=(TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),),
            ),
        )
        result = build_market_data_fetch_dry_run(
            db_path=self.db_path,
            tickers=['CAMBI.OL'],
            start_date=date(2026, 6, 19),
            end_date=date(2026, 8, 17),
            source_name='synthetic',
            source_override=source,
        )
        row = result.normalized_rows[0]
        self.assertEqual(row.raw_close, 104.0)
        self.assertEqual(row.adjusted_close, 94.0)
        self.assertEqual(result.dry_run.valid_row_count, 1)

    def test_adjusted_close_outside_raw_high_low_is_allowed(self) -> None:
        initialize_market_data_schema(self.db_path)
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(adjusted_close=90.0),),
                statuses=(TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),),
            ),
        )
        result = build_market_data_fetch_dry_run(
            db_path=self.db_path,
            tickers=['CAMBI.OL'],
            start_date=date(2026, 6, 19),
            end_date=date(2026, 8, 17),
            source_name='synthetic',
            source_override=source,
        )
        self.assertEqual(result.dry_run.invalid_row_count, 0)

    def test_raw_ohlc_inversion_is_rejected_by_validator(self) -> None:
        initialize_market_data_schema(self.db_path)
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(raw_high=98.0, raw_low=99.0),),
                statuses=(TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),),
            ),
        )
        result = build_market_data_fetch_dry_run(
            db_path=self.db_path,
            tickers=['CAMBI.OL'],
            start_date=date(2026, 6, 19),
            end_date=date(2026, 8, 17),
            source_name='synthetic',
            source_override=source,
        )
        self.assertEqual(result.dry_run.invalid_row_count, 1)
        self.assertIn('raw_high_lower_than_raw_low', result.dry_run.invalid_reasons)

    def test_missing_adj_close_falls_back_to_close_with_warning(self) -> None:
        initialize_market_data_schema(self.db_path)
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(adjusted_close=None, warning_codes=('adjusted_close_fallback_to_raw_close',)),),
                statuses=(TickerFetchStatus('CAMBI.OL', True, 1, 'fetched', ('adjusted_close_fallback_to_raw_close',)),),
            ),
        )
        result = build_market_data_fetch_dry_run(
            db_path=self.db_path,
            tickers=['CAMBI.OL'],
            start_date=date(2026, 6, 19),
            end_date=date(2026, 8, 17),
            source_name='synthetic',
            source_override=source,
        )
        self.assertEqual(result.normalized_rows[0].adjusted_close, result.normalized_rows[0].raw_close)
        self.assertIn('adjusted_close_fallback_to_raw_close', result.normalized_rows[0].warning_codes)

    def test_yahoo_multiindex_row_mapping_is_normalized_correctly(self) -> None:
        row = _normalize_yahoo_row(
            ticker='CAMBI.OL',
            index_value=date(2026, 6, 19),
            row_mapping={
                ('Adj Close', 'CAMBI.OL'): 22.5,
                ('Close', 'CAMBI.OL'): 22.5,
                ('High', 'CAMBI.OL'): 22.5,
                ('Low', 'CAMBI.OL'): 20.0,
                ('Open', 'CAMBI.OL'): 20.0,
                ('Volume', 'CAMBI.OL'): 18547.0,
            },
        )
        self.assertEqual(row.price_date, '2026-06-19')
        self.assertEqual(row.raw_open, 20.0)
        self.assertEqual(row.raw_high, 22.5)
        self.assertEqual(row.raw_low, 20.0)
        self.assertEqual(row.raw_close, 22.5)
        self.assertEqual(row.adjusted_close, 22.5)
        self.assertEqual(row.volume, 18547.0)

    def test_dry_run_fetch_writes_no_rows(self) -> None:
        initialize_market_data_schema(self.db_path)
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(),),
                statuses=(TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),),
            ),
        )
        result = build_market_data_fetch_dry_run(
            db_path=self.db_path,
            tickers=['CAMBI.OL'],
            start_date=date(2026, 6, 19),
            end_date=date(2026, 8, 17),
            source_name='synthetic',
            source_override=source,
        )
        inspection = inspect_market_data_schema(self.db_path)
        self.assertEqual(result.dry_run.would_insert, 1)
        self.assertEqual(inspection.row_count, 0)

    def test_explicit_legacy_production_db_path_is_refused(self) -> None:
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(rows=(), statuses=()),
        )
        with self.assertRaises(ValueError):
            build_market_data_fetch_dry_run(
                db_path=LEGACY_PRODUCTION_DB_PATH,
                tickers=['CAMBI.OL'],
                start_date=date(2026, 6, 19),
                end_date=date(2026, 8, 17),
                source_name='synthetic',
                source_override=source,
            )

    def test_cli_writes_only_expected_report_files(self) -> None:
        initialize_market_data_schema(self.db_path)
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(),),
                statuses=(TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),),
            ),
        )
        with patch('tradetool.diagnostics.market_data_fetch_dry_run.build_market_data_source', return_value=source):
            exit_code = market_data_fetch_dry_run_cli_main([
                '--db-path', str(self.db_path),
                '--tickers', 'CAMBI.OL',
                '--start-date', '2026-06-19',
                '--source', 'synthetic',
                '--out-dir', str(self.out_dir),
            ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            sorted(path.name for path in self.out_dir.iterdir()),
            [
                'invalid_rows.csv',
                'market_data_fetch_dry_run_summary.json',
                'market_data_fetch_dry_run_summary.md',
                'normalized_rows_sample.csv',
                'ticker_fetch_status.csv',
            ],
        )

    def test_missing_ticker_is_reported(self) -> None:
        initialize_market_data_schema(self.db_path)
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(ticker='CAMBI.OL'),),
                statuses=(
                    TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),
                    TickerFetchStatus('GOD.OL', False, 0, 'missing', message='No rows returned'),
                ),
            ),
        )
        result = build_market_data_fetch_dry_run(
            db_path=self.db_path,
            tickers=['CAMBI.OL', 'GOD.OL'],
            start_date=date(2026, 6, 19),
            end_date=date(2026, 8, 17),
            source_name='synthetic',
            source_override=source,
        )
        self.assertEqual(result.fetched_tickers, ('CAMBI.OL',))
        self.assertEqual(result.missing_tickers, ('GOD.OL',))

    def test_provider_dependency_failure_is_reported_clearly(self) -> None:
        initialize_market_data_schema(self.db_path)
        with patch('tradetool.data.market_data_source.YahooMarketDataSource.fetch_daily_rows', side_effect=MarketDataSourceDependencyError('yfinance missing')):
            result = build_market_data_fetch_dry_run(
                db_path=self.db_path,
                tickers=['CAMBI.OL'],
                start_date=date(2026, 6, 19),
                end_date=date(2026, 8, 17),
                source_name='yahoo',
            )
        self.assertEqual(result.provider_error, 'yfinance missing')
        self.assertEqual(result.dry_run.valid_row_count, 0)
        self.assertEqual(result.missing_tickers, ('CAMBI.OL',))

    def test_no_network_is_required_for_unit_tests(self) -> None:
        source_module = Path('src/tradetool/data/market_data_source.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('requests.get', source_module)
        self.assertNotIn('urllib.request', source_module)
        self.assertNotIn('httpx.', source_module)

    def test_no_ranking_feature_policy_candidate_type_ml_or_holdings_logic_is_changed(self) -> None:
        ranking_source = Path('src/tradetool/ranking/baseline.py').read_text(encoding='utf-8')
        candidate_source = Path('src/tradetool/policy/candidate_type.py').read_text(encoding='utf-8')
        trade_policy_source = Path('src/tradetool/policy/trade_policy.py').read_text(encoding='utf-8')
        fetch_source = Path('src/tradetool/diagnostics/market_data_fetch_dry_run.py').read_text(encoding='utf-8').lower()
        self.assertIn("BASELINE_RANKING_ENGINE_ID = 'baseline_v0_price_volume_rs'", ranking_source)
        self.assertIn("CANDIDATE_TYPE_ENGINE_ID = 'candidate_type_v0_diagnostic'", candidate_source)
        self.assertIn("TRADE_POLICY_ENGINE_ID = 'trade_policy_v1_balanced_diagnostic'", trade_policy_source)
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertNotIn('holdings_signal', fetch_source)
        self.assertNotIn('ml_score', fetch_source)

    def test_summary_outputs_can_be_written(self) -> None:
        initialize_market_data_schema(self.db_path)
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(),),
                statuses=(TickerFetchStatus('CAMBI.OL', True, 1, 'fetched'),),
            ),
        )
        result = build_market_data_fetch_dry_run(
            db_path=self.db_path,
            tickers=['CAMBI.OL'],
            start_date=date(2026, 6, 19),
            end_date=date(2026, 8, 17),
            source_name='synthetic',
            source_override=source,
        )
        write_market_data_fetch_dry_run_outputs(result=result, out_dir=self.out_dir)
        summary = json.loads((self.out_dir / 'market_data_fetch_dry_run_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['valid_normalized_rows'], 1)
        with (self.out_dir / 'ticker_fetch_status.csv').open('r', encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]['ticker'], 'CAMBI.OL')

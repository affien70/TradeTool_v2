from __future__ import annotations

import csv
import json
import unittest
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from unittest.mock import patch

from tradetool.data.market_data_source import MarketDataSourceFetchResult, SourceMarketDataRow, TickerFetchStatus
from tradetool.diagnostics.ohlc_tolerance_audit import build_ohlc_tolerance_audit, write_ohlc_tolerance_audit_outputs
from tradetool.diagnostics.ohlc_tolerance_audit_cli import main as ohlc_tolerance_audit_cli_main
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import TRADE_POLICY_ENGINE_ID


@dataclass
class _SyntheticSource:
    source_name: str
    result: MarketDataSourceFetchResult

    def fetch_daily_rows(self, *, tickers, start_date, end_date):
        return self.result


def _result_with_rows(rows: tuple[SourceMarketDataRow, ...], statuses: tuple[TickerFetchStatus, ...]):
    return MarketDataSourceFetchResult(
        source_name='synthetic',
        requested_tickers=tuple(status.ticker for status in statuses),
        rows=rows,
        ticker_statuses=statuses,
    )


def _sample_source_row(**overrides):
    payload = {
        'ticker': 'SNTIA.OL',
        'price_date': '2026-09-09',
        'raw_open': 86.9,
        'raw_high': 87.0,
        'raw_low': 85.1,
        'raw_close': 85.0,
        'adjusted_close': 85.0,
        'volume': 416742.0,
        'data_source': 'yahoo',
        'warning_codes': (),
    }
    payload.update(overrides)
    return SourceMarketDataRow(**payload)


class OHLCToleranceAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.out_dir = Path('/tmp/tradetool_v2_ohlc_tolerance_audit_output')
        if self.out_dir.exists():
            for child in self.out_dir.iterdir():
                child.unlink()
            self.out_dir.rmdir()

    def tearDown(self) -> None:
        if self.out_dir.exists():
            for child in self.out_dir.iterdir():
                child.unlink()
            self.out_dir.rmdir()

    def _build(self, rows: tuple[SourceMarketDataRow, ...]):
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=rows,
                statuses=tuple(TickerFetchStatus(row.ticker, True, 1, 'fetched') for row in rows),
            ),
        )
        return build_ohlc_tolerance_audit(
            tickers=[row.ticker for row in rows],
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 9),
            source_name='synthetic',
            source_override=source,
        )

    def test_small_raw_low_higher_than_raw_close_violation_amounts_are_reported(self) -> None:
        result = self._build((_sample_source_row(raw_low=85.1, raw_close=85.0),))
        violation = result.violations[0]
        self.assertEqual(violation.reason, 'raw_low_higher_than_raw_close')
        self.assertAlmostEqual(violation.absolute_violation_amount, 0.1)
        self.assertAlmostEqual(violation.relative_violation_amount_vs_raw_close, 0.1 / 85.0)

    def test_small_raw_high_lower_than_raw_close_violation_amounts_are_reported(self) -> None:
        result = self._build((_sample_source_row(raw_high=84.95, raw_close=85.0),))
        violation = next(row for row in result.violations if row.reason == 'raw_high_lower_than_raw_close')
        self.assertAlmostEqual(violation.absolute_violation_amount, 0.05)
        self.assertAlmostEqual(violation.relative_violation_amount_vs_raw_close, 0.05 / 85.0)

    def test_high_low_inversion_is_not_considered_tolerable(self) -> None:
        result = self._build((_sample_source_row(raw_high=84.0, raw_low=85.1, raw_close=85.0),))
        scenario = next(row for row in result.scenarios if row.scenario == 'combined_abs_0_25_rel_0_005')
        self.assertEqual(scenario.tolerated_invalid_rows, 0)
        self.assertEqual(scenario.remaining_invalid_rows, 1)

    def test_nonpositive_prices_are_not_considered_tolerable(self) -> None:
        result = self._build((_sample_source_row(raw_low=0.0),))
        scenario = next(row for row in result.scenarios if row.scenario == 'combined_abs_0_25_rel_0_005')
        self.assertEqual(scenario.tolerated_invalid_rows, 0)
        self.assertEqual(scenario.remaining_invalid_rows, 1)

    def test_adjusted_close_outside_raw_ohlc_is_not_raw_ohlc_violation(self) -> None:
        result = self._build((_sample_source_row(raw_low=84.0, raw_close=85.0, adjusted_close=60.0),))
        self.assertEqual(result.strict_invalid_rows, 0)
        self.assertEqual(result.violation_count, 0)

    def test_tolerance_scenarios_count_tolerated_and_remaining_invalid_rows(self) -> None:
        result = self._build((
            _sample_source_row(ticker='SNTIA.OL', raw_low=85.1, raw_close=85.0),
            _sample_source_row(ticker='CAMBI.OL', raw_low=86.0, raw_close=85.0),
        ))
        scenario = next(row for row in result.scenarios if row.scenario == 'absolute_0_10')
        self.assertEqual(scenario.strict_invalid_rows, 2)
        self.assertEqual(scenario.tolerated_invalid_rows, 1)
        self.assertEqual(scenario.remaining_invalid_rows, 1)
        self.assertEqual(scenario.affected_tickers, ('CAMBI.OL',))

    def test_sntia_like_small_violation_passes_under_conservative_scenario(self) -> None:
        result = self._build((_sample_source_row(),))
        scenario = next(row for row in result.scenarios if row.scenario == 'combined_abs_0_10_rel_0_0025')
        self.assertTrue(scenario.sntia_2026_09_09_would_pass)
        self.assertEqual(result.recommendation, 'apply_conservative_ohlc_tolerance')

    def test_cli_writes_only_expected_report_files(self) -> None:
        source = _SyntheticSource(
            source_name='synthetic',
            result=_result_with_rows(
                rows=(_sample_source_row(),),
                statuses=(TickerFetchStatus('SNTIA.OL', True, 1, 'fetched'),),
            ),
        )
        with patch('tradetool.diagnostics.ohlc_tolerance_audit.build_market_data_source', return_value=source):
            exit_code = ohlc_tolerance_audit_cli_main([
                '--tickers', 'SNTIA.OL',
                '--start-date', '2026-09-01',
                '--source', 'synthetic',
                '--out-dir', str(self.out_dir),
            ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            sorted(path.name for path in self.out_dir.iterdir()),
            [
                'ohlc_tolerance_audit_summary.json',
                'ohlc_tolerance_audit_summary.md',
                'ohlc_tolerance_by_ticker.csv',
                'ohlc_tolerance_scenarios.csv',
                'ohlc_tolerance_violations.csv',
            ],
        )

    def test_no_db_files_are_created_and_no_db_writes_occur(self) -> None:
        db_path = Path('/tmp/tradetool_v2_ohlc_tolerance_audit_should_not_exist.sqlite')
        if db_path.exists():
            db_path.unlink()
        self._build((_sample_source_row(),))
        self.assertFalse(db_path.exists())
        source = Path('src/tradetool/diagnostics/ohlc_tolerance_audit.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('sqlite3', source)
        self.assertNotIn('initialize_market_data_schema', source)
        self.assertNotIn('write_market_data_rows_for_test', source)

    def test_summary_can_be_written_and_read(self) -> None:
        result = self._build((_sample_source_row(),))
        write_ohlc_tolerance_audit_outputs(result=result, out_dir=self.out_dir)
        summary = json.loads((self.out_dir / 'ohlc_tolerance_audit_summary.json').read_text(encoding='utf-8'))
        with (self.out_dir / 'ohlc_tolerance_violations.csv').open('r', encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(summary['strict_invalid_rows'], 1)
        self.assertEqual(rows[0]['ticker'], 'SNTIA.OL')

    def test_no_ranking_feature_policy_candidate_type_ml_or_holdings_logic_is_changed(self) -> None:
        ranking_source = Path('src/tradetool/ranking/baseline.py').read_text(encoding='utf-8')
        feature_source = Path('src/tradetool/features/raw.py').read_text(encoding='utf-8')
        candidate_source = Path('src/tradetool/policy/candidate_type.py').read_text(encoding='utf-8')
        trade_policy_source = Path('src/tradetool/policy/trade_policy.py').read_text(encoding='utf-8')
        audit_source = Path('src/tradetool/diagnostics/ohlc_tolerance_audit.py').read_text(encoding='utf-8').lower()
        self.assertIn("BASELINE_RANKING_ENGINE_ID = 'baseline_v0_price_volume_rs'", ranking_source)
        self.assertIn('return (latest_value / base_value) - 1.0', feature_source)
        self.assertIn("CANDIDATE_TYPE_ENGINE_ID = 'candidate_type_v0_diagnostic'", candidate_source)
        self.assertIn("TRADE_POLICY_ENGINE_ID = 'trade_policy_v1_balanced_diagnostic'", trade_policy_source)
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertNotIn('trade_signal', audit_source)
        self.assertNotIn('holdings_signal', audit_source)
        self.assertNotIn('ml_score', audit_source)

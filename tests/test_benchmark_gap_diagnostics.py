from __future__ import annotations

import csv
import json
import unittest
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from tradetool.data.market_data_source import MarketDataSourceFetchResult, SourceMarketDataRow, TickerFetchStatus
from tradetool.diagnostics.benchmark_gap import build_benchmark_gap_report, write_benchmark_gap_outputs
from tradetool.diagnostics.benchmark_gap_cli import main as benchmark_gap_cli_main


@dataclass
class _SyntheticSource:
    source_name: str
    result: MarketDataSourceFetchResult

    def fetch_daily_rows(self, *, tickers, start_date, end_date):
        return self.result


def _rows(ticker: str, *, start: date, count: int, adjusted: bool = True) -> tuple[SourceMarketDataRow, ...]:
    rows: list[SourceMarketDataRow] = []
    for index in range(count):
        price_date = start + timedelta(days=index)
        raw_close = 100.0 + index
        adjusted_close = raw_close if adjusted else None
        warning_codes = () if adjusted else ('adjusted_close_fallback_to_raw_close',)
        rows.append(
            SourceMarketDataRow(
                ticker=ticker,
                price_date=price_date.isoformat(),
                raw_open=raw_close,
                raw_high=raw_close + 1.0,
                raw_low=raw_close - 1.0,
                raw_close=raw_close,
                adjusted_close=adjusted_close,
                volume=1000.0,
                data_source='synthetic',
                warning_codes=warning_codes,
            )
        )
    return tuple(rows)


def _fetch_result(*, rows: tuple[SourceMarketDataRow, ...], missing: tuple[str, ...] = ()) -> MarketDataSourceFetchResult:
    row_counts: dict[str, int] = {}
    for row in rows:
        row_counts[row.ticker] = row_counts.get(row.ticker, 0) + 1
    statuses = [
        TickerFetchStatus(ticker=ticker, fetched=True, row_count=count, status='fetched')
        for ticker, count in sorted(row_counts.items())
    ]
    statuses.extend(TickerFetchStatus(ticker=ticker, fetched=False, row_count=0, status='missing', message='No rows') for ticker in missing)
    return MarketDataSourceFetchResult(
        source_name='synthetic',
        requested_tickers=tuple(sorted((*row_counts.keys(), *missing))),
        rows=rows,
        ticker_statuses=tuple(statuses),
    )


class BenchmarkGapDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.out_dir = Path('/tmp/tradetool_v2_benchmark_gap_unit_output')
        if self.out_dir.exists():
            for child in self.out_dir.iterdir():
                child.unlink()
            self.out_dir.rmdir()

    def tearDown(self) -> None:
        if self.out_dir.exists():
            for child in self.out_dir.iterdir():
                child.unlink()
            self.out_dir.rmdir()

    def _build(self, rows: tuple[SourceMarketDataRow, ...], benchmarks=None, missing=()):
        source = _SyntheticSource(source_name='synthetic', result=_fetch_result(rows=rows, missing=missing))
        return build_benchmark_gap_report(
            tickers=['AAA.OL', 'BBB.OL'],
            benchmark_candidates=benchmarks or ['^OSEAX', '^OSEBX'],
            start_date=date(2026, 1, 1),
            end_date=date(2026, 9, 9),
            source_name='synthetic',
            source_override=source,
        )

    def test_cli_writes_exactly_expected_report_files(self) -> None:
        rows = (
            *_rows('AAA.OL', start=date(2026, 1, 1), count=252),
            *_rows('^OSEAX', start=date(2026, 1, 1), count=252),
        )
        source = _SyntheticSource(source_name='synthetic', result=_fetch_result(rows=rows, missing=('BBB.OL',)))
        with patch('tradetool.diagnostics.benchmark_gap.build_market_data_source', return_value=source):
            exit_code = benchmark_gap_cli_main([
                '--tickers', 'AAA.OL', 'BBB.OL',
                '--benchmark-candidates', '^OSEAX',
                '--start-date', '2026-01-01',
                '--source', 'synthetic',
                '--out-dir', str(self.out_dir),
            ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            sorted(path.name for path in self.out_dir.iterdir()),
            [
                'benchmark_candidate_coverage.csv',
                'benchmark_gap_summary.json',
                'benchmark_gap_summary.md',
                'benchmark_recommendation.csv',
                'ticker_vs_benchmark_alignment.csv',
            ],
        )

    def test_current_benchmark_with_lag_is_reported(self) -> None:
        result = self._build((
            *_rows('AAA.OL', start=date(2026, 1, 1), count=260),
            *_rows('BBB.OL', start=date(2026, 1, 1), count=260),
            *_rows('^OSEAX', start=date(2026, 1, 1), count=252),
        ))
        row = next(alignment for alignment in result.alignments if alignment.stock_ticker == 'AAA.OL' and alignment.benchmark_candidate == '^OSEAX')
        self.assertEqual(row.benchmark_lag_days, 8)
        self.assertTrue(row.benchmark_lag_warning)
        self.assertEqual(row.aligned_row_count, 252)

    def test_alternative_benchmark_with_fresher_data_is_preferred(self) -> None:
        result = self._build((
            *_rows('AAA.OL', start=date(2026, 1, 1), count=260),
            *_rows('BBB.OL', start=date(2026, 1, 1), count=260),
            *_rows('^OSEAX', start=date(2026, 1, 1), count=252),
            *_rows('^OSEBX', start=date(2026, 1, 1), count=260),
        ))
        self.assertEqual(result.recommendation.recommendation, 'switch_to_alternative_yahoo_benchmark')
        self.assertEqual(result.recommendation.preferred_benchmark, '^OSEBX')

    def test_missing_benchmark_candidate_is_reported_clearly(self) -> None:
        result = self._build((
            *_rows('AAA.OL', start=date(2026, 1, 1), count=252),
            *_rows('BBB.OL', start=date(2026, 1, 1), count=252),
        ), benchmarks=['^MISSING'], missing=('^MISSING',))
        coverage = result.benchmark_coverage[0]
        self.assertEqual(coverage.fetch_status, 'missing')
        self.assertEqual(coverage.missing_or_error_reason, 'No rows')
        self.assertEqual(result.recommendation.recommendation, 'blocked_no_usable_benchmark')

    def test_enough_aligned_rows_are_calculated_correctly(self) -> None:
        result = self._build((
            *_rows('AAA.OL', start=date(2026, 1, 1), count=252),
            *_rows('BBB.OL', start=date(2026, 1, 1), count=251),
            *_rows('^OSEAX', start=date(2026, 1, 1), count=252),
        ), benchmarks=['^OSEAX'])
        rows = {alignment.stock_ticker: alignment for alignment in result.alignments}
        self.assertTrue(rows['AAA.OL'].enough_aligned_rows_for_252_features)
        self.assertFalse(rows['BBB.OL'].enough_aligned_rows_for_252_features)

    def test_benchmark_lag_days_are_calculated_correctly(self) -> None:
        result = self._build((
            *_rows('AAA.OL', start=date(2026, 1, 1), count=255),
            *_rows('BBB.OL', start=date(2026, 1, 1), count=255),
            *_rows('^OSEAX', start=date(2026, 1, 1), count=252),
        ), benchmarks=['^OSEAX'])
        self.assertEqual(result.alignments[0].benchmark_lag_days, 3)
        self.assertFalse(result.alignments[0].benchmark_lag_warning)

    def test_adjusted_close_fallback_is_reported(self) -> None:
        result = self._build((
            *_rows('AAA.OL', start=date(2026, 1, 1), count=252),
            *_rows('BBB.OL', start=date(2026, 1, 1), count=252),
            *_rows('^OSEAX', start=date(2026, 1, 1), count=252, adjusted=False),
        ), benchmarks=['^OSEAX'])
        self.assertEqual(result.benchmark_coverage[0].adjusted_close_status, 'fallback_to_raw_close')

    def test_no_db_path_is_required_and_no_db_files_are_created(self) -> None:
        db_path = Path('/tmp/tradetool_v2_benchmark_gap_should_not_exist.sqlite')
        if db_path.exists():
            db_path.unlink()
        result = self._build((
            *_rows('AAA.OL', start=date(2026, 1, 1), count=252),
            *_rows('BBB.OL', start=date(2026, 1, 1), count=252),
            *_rows('^OSEAX', start=date(2026, 1, 1), count=252),
        ), benchmarks=['^OSEAX'])
        self.assertEqual(result.recommendation.recommendation, 'keep_yahoo_oseax_with_lag_warning')
        self.assertFalse(db_path.exists())

    def test_outputs_include_no_ranking_policy_candidate_type_ml_or_holdings(self) -> None:
        source = Path('src/tradetool/diagnostics/benchmark_gap.py').read_text(encoding='utf-8').lower()
        cli_source = Path('src/tradetool/diagnostics/benchmark_gap_cli.py').read_text(encoding='utf-8').lower()
        combined = source + cli_source
        self.assertNotIn('sqlite3', combined)
        self.assertNotIn('db_path', combined)
        self.assertNotIn('trade_signal', combined)
        self.assertNotIn('candidate_type', combined)
        self.assertNotIn('ml_score', combined)
        self.assertNotIn('holdings_signal', combined)
        self.assertNotIn('baseline_rank', combined)
        self.assertNotIn('raw_rank', combined)

    def test_summary_and_csv_can_be_written_and_read(self) -> None:
        result = self._build((
            *_rows('AAA.OL', start=date(2026, 1, 1), count=252),
            *_rows('BBB.OL', start=date(2026, 1, 1), count=252),
            *_rows('^OSEAX', start=date(2026, 1, 1), count=252),
        ), benchmarks=['^OSEAX'])
        write_benchmark_gap_outputs(result=result, out_dir=self.out_dir)
        summary = json.loads((self.out_dir / 'benchmark_gap_summary.json').read_text(encoding='utf-8'))
        with (self.out_dir / 'benchmark_candidate_coverage.csv').open('r', encoding='utf-8', newline='') as handle:
            coverage_rows = list(csv.DictReader(handle))
        self.assertEqual(summary['recommendation']['recommendation'], 'keep_yahoo_oseax_with_lag_warning')
        self.assertEqual(summary['stock_coverage'][0]['candidate_ticker'], 'AAA.OL')
        self.assertEqual(coverage_rows[0]['candidate_ticker'], '^OSEAX')

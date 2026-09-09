from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from tradetool.data.market_data_schema import _validate_market_data_row_with_warnings
from tradetool.data.market_data_source import (
    MarketDataSource,
    MarketDataSourceDependencyError,
    MarketDataSourceFetchResult,
    SourceMarketDataRow,
    TickerFetchStatus,
    build_market_data_source,
    normalize_source_rows_for_v2,
)

MIN_ALIGNED_ROWS_FOR_FEATURES = 252
MAX_ACCEPTABLE_BENCHMARK_LAG_DAYS = 5
BENCHMARK_CURRENTNESS_MATERIALITY_DAYS = 5


@dataclass(frozen=True, slots=True)
class BenchmarkCandidateCoverageRow:
    candidate_ticker: str
    fetch_status: str
    row_count: int
    first_date: str | None
    latest_date: str | None
    missing_or_error_reason: str | None
    valid_normalized_rows: int
    invalid_normalized_rows: int
    adjusted_close_status: str

    def to_dict(self) -> dict[str, object]:
        return {
            'candidate_ticker': self.candidate_ticker,
            'fetch_status': self.fetch_status,
            'row_count': self.row_count,
            'first_date': self.first_date,
            'latest_date': self.latest_date,
            'missing_or_error_reason': self.missing_or_error_reason,
            'valid_normalized_rows': self.valid_normalized_rows,
            'invalid_normalized_rows': self.invalid_normalized_rows,
            'adjusted_close_status': self.adjusted_close_status,
        }


@dataclass(frozen=True, slots=True)
class TickerBenchmarkAlignmentRow:
    stock_ticker: str
    stock_latest_date: str | None
    benchmark_candidate: str
    benchmark_latest_date: str | None
    common_aligned_max_date: str | None
    aligned_row_count: int
    benchmark_lag_days: int | None
    enough_aligned_rows_for_252_features: bool
    benchmark_lag_warning: bool

    def to_dict(self) -> dict[str, object]:
        return {
            'stock_ticker': self.stock_ticker,
            'stock_latest_date': self.stock_latest_date,
            'benchmark_candidate': self.benchmark_candidate,
            'benchmark_latest_date': self.benchmark_latest_date,
            'common_aligned_max_date': self.common_aligned_max_date,
            'aligned_row_count': self.aligned_row_count,
            'benchmark_lag_days': self.benchmark_lag_days,
            'enough_aligned_rows_for_252_features': self.enough_aligned_rows_for_252_features,
            'benchmark_lag_warning': self.benchmark_lag_warning,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkRecommendationRow:
    recommendation: str
    preferred_benchmark: str | None
    current_benchmark: str | None
    stock_latest_date: str | None
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            'recommendation': self.recommendation,
            'preferred_benchmark': self.preferred_benchmark,
            'current_benchmark': self.current_benchmark,
            'stock_latest_date': self.stock_latest_date,
            'reason': self.reason,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkGapResult:
    source_name: str
    requested_stock_tickers: tuple[str, ...]
    benchmark_candidates: tuple[str, ...]
    fetched_stock_tickers: tuple[str, ...]
    missing_stock_tickers: tuple[str, ...]
    provider_warning: str | None
    provider_error: str | None
    stock_latest_date: str | None
    stock_coverage: tuple[BenchmarkCandidateCoverageRow, ...]
    benchmark_coverage: tuple[BenchmarkCandidateCoverageRow, ...]
    alignments: tuple[TickerBenchmarkAlignmentRow, ...]
    recommendation: BenchmarkRecommendationRow
    ticker_statuses: tuple[TickerFetchStatus, ...]
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'source_name': self.source_name,
            'requested_stock_tickers': list(self.requested_stock_tickers),
            'benchmark_candidates': list(self.benchmark_candidates),
            'fetched_stock_tickers': list(self.fetched_stock_tickers),
            'missing_stock_tickers': list(self.missing_stock_tickers),
            'provider_warning': self.provider_warning,
            'provider_error': self.provider_error,
            'stock_latest_date': self.stock_latest_date,
            'stock_coverage_count': len(self.stock_coverage),
            'benchmark_candidate_count': len(self.benchmark_coverage),
            'benchmark_lag_warning_count': sum(1 for row in self.alignments if row.benchmark_lag_warning),
            'usable_benchmark_count': len(_usable_benchmark_tickers(self.benchmark_coverage, self.alignments)),
            'stock_coverage': [row.to_dict() for row in self.stock_coverage],
            'benchmark_coverage': [row.to_dict() for row in self.benchmark_coverage],
            'recommendation': self.recommendation.to_dict(),
            'generated_at_utc': self.generated_at_utc,
        }


def build_benchmark_gap_report(
    *,
    tickers: Sequence[str],
    benchmark_candidates: Sequence[str],
    start_date: date,
    end_date: date,
    source_name: str,
    source_override: MarketDataSource | None = None,
) -> BenchmarkGapResult:
    stock_tickers = _normalize_tickers(tickers)
    benchmarks = _normalize_tickers(benchmark_candidates)
    requested_tickers = tuple(dict.fromkeys((*stock_tickers, *benchmarks)))
    generated_at_utc = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    fetch_result = _fetch_rows(
        tickers=requested_tickers,
        start_date=start_date,
        end_date=end_date,
        source_name=source_name,
        source_override=source_override,
    )
    rows_by_ticker = _group_rows_by_ticker(fetch_result.rows)
    valid_rows_by_ticker = _group_valid_normalized_rows(
        fetch_result.rows,
        generated_at_utc=generated_at_utc,
    )
    status_by_ticker = {status.ticker: status for status in fetch_result.ticker_statuses}
    stock_coverage = tuple(
        _build_coverage_row(ticker=ticker, rows=rows_by_ticker.get(ticker, ()), valid_rows=valid_rows_by_ticker.get(ticker, ()), status=status_by_ticker.get(ticker))
        for ticker in stock_tickers
    )
    benchmark_coverage = tuple(
        _build_coverage_row(ticker=ticker, rows=rows_by_ticker.get(ticker, ()), valid_rows=valid_rows_by_ticker.get(ticker, ()), status=status_by_ticker.get(ticker))
        for ticker in benchmarks
    )
    alignments = tuple(
        _build_alignment_row(
            stock_ticker=stock_ticker,
            stock_rows=valid_rows_by_ticker.get(stock_ticker, ()),
            benchmark_ticker=benchmark_ticker,
            benchmark_rows=valid_rows_by_ticker.get(benchmark_ticker, ()),
        )
        for stock_ticker in stock_tickers
        for benchmark_ticker in benchmarks
    )
    recommendation = _build_recommendation(
        stock_coverage=stock_coverage,
        benchmark_coverage=benchmark_coverage,
        alignments=alignments,
        current_benchmark=benchmarks[0] if benchmarks else None,
    )
    fetched_stock_tickers = tuple(sorted(row.candidate_ticker for row in stock_coverage if row.fetch_status == 'fetched'))
    missing_stock_tickers = tuple(sorted(row.candidate_ticker for row in stock_coverage if row.fetch_status != 'fetched'))
    return BenchmarkGapResult(
        source_name=source_name,
        requested_stock_tickers=stock_tickers,
        benchmark_candidates=benchmarks,
        fetched_stock_tickers=fetched_stock_tickers,
        missing_stock_tickers=missing_stock_tickers,
        provider_warning=fetch_result.provider_warning,
        provider_error=fetch_result.provider_error,
        stock_latest_date=_max_date_string(row.latest_date for row in stock_coverage),
        stock_coverage=stock_coverage,
        benchmark_coverage=benchmark_coverage,
        alignments=alignments,
        recommendation=recommendation,
        ticker_statuses=fetch_result.ticker_statuses,
        generated_at_utc=generated_at_utc,
    )


def write_benchmark_gap_outputs(*, result: BenchmarkGapResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'benchmark_gap_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'benchmark_gap_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')
    _write_csv(out_dir / 'benchmark_candidate_coverage.csv', [row.to_dict() for row in result.benchmark_coverage])
    _write_csv(out_dir / 'ticker_vs_benchmark_alignment.csv', [row.to_dict() for row in result.alignments])
    _write_csv(out_dir / 'benchmark_recommendation.csv', [result.recommendation.to_dict()])


def _fetch_rows(
    *,
    tickers: tuple[str, ...],
    start_date: date,
    end_date: date,
    source_name: str,
    source_override: MarketDataSource | None,
) -> MarketDataSourceFetchResult:
    try:
        source = source_override or build_market_data_source(source_name)
        return source.fetch_daily_rows(tickers=tickers, start_date=start_date, end_date=end_date)
    except MarketDataSourceDependencyError as exc:
        return MarketDataSourceFetchResult(
            source_name=source_name,
            requested_tickers=tickers,
            rows=(),
            ticker_statuses=tuple(
                TickerFetchStatus(
                    ticker=ticker,
                    fetched=False,
                    row_count=0,
                    status='provider_unavailable',
                    message=str(exc),
                )
                for ticker in tickers
            ),
            provider_error=str(exc),
        )


def _build_coverage_row(
    *,
    ticker: str,
    rows: tuple[SourceMarketDataRow, ...],
    valid_rows,
    status: TickerFetchStatus | None,
) -> BenchmarkCandidateCoverageRow:
    invalid_count = max(0, len(rows) - len(valid_rows))
    return BenchmarkCandidateCoverageRow(
        candidate_ticker=ticker,
        fetch_status=status.status if status is not None else 'missing',
        row_count=len(rows),
        first_date=None if not rows else rows[0].price_date,
        latest_date=None if not rows else rows[-1].price_date,
        missing_or_error_reason=None if status is None else status.message,
        valid_normalized_rows=len(valid_rows),
        invalid_normalized_rows=invalid_count,
        adjusted_close_status=_adjusted_close_status(rows),
    )


def _build_alignment_row(
    *,
    stock_ticker: str,
    stock_rows,
    benchmark_ticker: str,
    benchmark_rows,
) -> TickerBenchmarkAlignmentRow:
    stock_latest = None if not stock_rows else _row_date(stock_rows[-1].price_date)
    benchmark_latest = None if not benchmark_rows else _row_date(benchmark_rows[-1].price_date)
    common_dates = {_row_date(row.price_date) for row in stock_rows}.intersection(
        _row_date(row.price_date) for row in benchmark_rows
    )
    common_aligned_max_date = max(common_dates) if common_dates else None
    lag_days = None
    if stock_latest is not None and benchmark_latest is not None:
        lag_days = (stock_latest - benchmark_latest).days
    aligned_row_count = len(common_dates)
    return TickerBenchmarkAlignmentRow(
        stock_ticker=stock_ticker,
        stock_latest_date=None if stock_latest is None else stock_latest.isoformat(),
        benchmark_candidate=benchmark_ticker,
        benchmark_latest_date=None if benchmark_latest is None else benchmark_latest.isoformat(),
        common_aligned_max_date=None if common_aligned_max_date is None else common_aligned_max_date.isoformat(),
        aligned_row_count=aligned_row_count,
        benchmark_lag_days=lag_days,
        enough_aligned_rows_for_252_features=aligned_row_count >= MIN_ALIGNED_ROWS_FOR_FEATURES,
        benchmark_lag_warning=lag_days is not None and lag_days > MAX_ACCEPTABLE_BENCHMARK_LAG_DAYS,
    )


def _build_recommendation(
    *,
    stock_coverage: tuple[BenchmarkCandidateCoverageRow, ...],
    benchmark_coverage: tuple[BenchmarkCandidateCoverageRow, ...],
    alignments: tuple[TickerBenchmarkAlignmentRow, ...],
    current_benchmark: str | None,
) -> BenchmarkRecommendationRow:
    stock_latest_date = _max_date_string(row.latest_date for row in stock_coverage)
    fetched_stocks = [row for row in stock_coverage if row.fetch_status == 'fetched']
    benchmark_by_ticker = {row.candidate_ticker: row for row in benchmark_coverage}
    usable = _usable_benchmark_tickers(benchmark_coverage, alignments)
    if not usable:
        if fetched_stocks and any(row.valid_normalized_rows > 0 for row in benchmark_coverage):
            return BenchmarkRecommendationRow(
                recommendation='needs_manual_benchmark_source',
                preferred_benchmark=None,
                current_benchmark=current_benchmark,
                stock_latest_date=stock_latest_date,
                reason='Stocks fetched but Yahoo benchmark candidates are stale, invalid, or lack enough aligned rows.',
            )
        return BenchmarkRecommendationRow(
            recommendation='blocked_no_usable_benchmark',
            preferred_benchmark=None,
            current_benchmark=current_benchmark,
            stock_latest_date=stock_latest_date,
            reason='No benchmark candidate had usable normalized data with enough aligned rows.',
        )

    current_usable = current_benchmark in usable if current_benchmark else False
    current_lag = _max_lag_for_benchmark(alignments, current_benchmark) if current_benchmark else None
    current_is_current_enough = current_usable and current_lag is not None and current_lag <= MAX_ACCEPTABLE_BENCHMARK_LAG_DAYS
    best = max(
        usable,
        key=lambda ticker: (
            benchmark_by_ticker[ticker].latest_date or '',
            -(_max_lag_for_benchmark(alignments, ticker) or 999999),
            ticker,
        ),
    )
    best_lag = _max_lag_for_benchmark(alignments, best)
    best_is_materially_fresher = (
        current_benchmark is not None
        and best != current_benchmark
        and _date_distance(benchmark_by_ticker.get(best), benchmark_by_ticker.get(current_benchmark))
        > BENCHMARK_CURRENTNESS_MATERIALITY_DAYS
    )
    if current_is_current_enough and not best_is_materially_fresher:
        return BenchmarkRecommendationRow(
            recommendation='keep_yahoo_oseax_with_lag_warning',
            preferred_benchmark=current_benchmark,
            current_benchmark=current_benchmark,
            stock_latest_date=stock_latest_date,
            reason='Current benchmark is usable and within the accepted lag window.',
        )
    if best_lag is not None and best_lag <= MAX_ACCEPTABLE_BENCHMARK_LAG_DAYS and (not current_usable or best_is_materially_fresher):
        return BenchmarkRecommendationRow(
            recommendation='switch_to_alternative_yahoo_benchmark',
            preferred_benchmark=best,
            current_benchmark=current_benchmark,
            stock_latest_date=stock_latest_date,
            reason='Alternative Yahoo benchmark is materially more current and has enough aligned rows.',
        )
    if fetched_stocks:
        return BenchmarkRecommendationRow(
            recommendation='needs_manual_benchmark_source',
            preferred_benchmark=None,
            current_benchmark=current_benchmark,
            stock_latest_date=stock_latest_date,
            reason='Yahoo benchmark candidates are usable for history but too stale for approving RS-based production ranking.',
        )
    return BenchmarkRecommendationRow(
        recommendation='blocked_no_usable_benchmark',
        preferred_benchmark=None,
        current_benchmark=current_benchmark,
        stock_latest_date=stock_latest_date,
        reason='Stock and benchmark coverage were not sufficient for benchmark decision.',
    )


def _usable_benchmark_tickers(
    benchmark_coverage: tuple[BenchmarkCandidateCoverageRow, ...],
    alignments: tuple[TickerBenchmarkAlignmentRow, ...],
) -> tuple[str, ...]:
    usable: list[str] = []
    for row in benchmark_coverage:
        if row.fetch_status != 'fetched' or row.valid_normalized_rows < MIN_ALIGNED_ROWS_FOR_FEATURES:
            continue
        candidate_alignments = [alignment for alignment in alignments if alignment.benchmark_candidate == row.candidate_ticker]
        if candidate_alignments and all(alignment.enough_aligned_rows_for_252_features for alignment in candidate_alignments):
            usable.append(row.candidate_ticker)
    return tuple(usable)


def _group_rows_by_ticker(rows: Sequence[SourceMarketDataRow]) -> dict[str, tuple[SourceMarketDataRow, ...]]:
    grouped: dict[str, list[SourceMarketDataRow]] = {}
    for row in rows:
        grouped.setdefault(row.ticker.strip().upper(), []).append(row)
    return {ticker: tuple(sorted(ticker_rows, key=lambda row: row.price_date)) for ticker, ticker_rows in grouped.items()}


def _group_valid_normalized_rows(
    rows: Sequence[SourceMarketDataRow],
    *,
    generated_at_utc: str,
):
    market_rows = normalize_source_rows_for_v2(
        rows=rows,
        created_at_utc=generated_at_utc,
        updated_at_utc=generated_at_utc,
    )
    grouped = {}
    for row in market_rows:
        validation = _validate_market_data_row_with_warnings(row)
        if validation.reasons:
            continue
        grouped.setdefault(row.ticker.strip().upper(), []).append(row)
    return {ticker: tuple(sorted(ticker_rows, key=lambda row: _row_date(row.price_date))) for ticker, ticker_rows in grouped.items()}


def _row_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _adjusted_close_status(rows: tuple[SourceMarketDataRow, ...]) -> str:
    if not rows:
        return 'missing'
    fallback_count = sum(1 for row in rows if 'adjusted_close_fallback_to_raw_close' in row.warning_codes)
    adjusted_count = sum(1 for row in rows if row.adjusted_close is not None and 'adjusted_close_fallback_to_raw_close' not in row.warning_codes)
    if fallback_count == len(rows):
        return 'fallback_to_raw_close'
    if adjusted_count == len(rows):
        return 'adjusted_close'
    if fallback_count or adjusted_count:
        return 'mixed'
    return 'missing_adjusted_close'


def _max_lag_for_benchmark(alignments: tuple[TickerBenchmarkAlignmentRow, ...], benchmark: str | None) -> int | None:
    lags = [
        row.benchmark_lag_days
        for row in alignments
        if row.benchmark_candidate == benchmark and row.benchmark_lag_days is not None
    ]
    return max(lags) if lags else None


def _date_distance(
    newer: BenchmarkCandidateCoverageRow | None,
    older: BenchmarkCandidateCoverageRow | None,
) -> int:
    if newer is None or older is None or newer.latest_date is None or older.latest_date is None:
        return 0
    return (date.fromisoformat(newer.latest_date) - date.fromisoformat(older.latest_date)).days


def _max_date_string(values) -> str | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _normalize_tickers(tickers: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ticker.strip().upper() for ticker in tickers if ticker.strip()))


def _render_summary_markdown(result: BenchmarkGapResult) -> str:
    reason_counts = Counter(row.fetch_status for row in result.benchmark_coverage)
    lines = [
        '# Benchmark Gap Summary',
        '',
        '## Scope',
        '',
        f'- Source: {result.source_name}',
        f'- Stock tickers: {", ".join(result.requested_stock_tickers) or "none"}',
        f'- Benchmark candidates: {", ".join(result.benchmark_candidates) or "none"}',
        '- Fetch only; no DB path is accepted and no DB writes are performed.',
        '- No ranking, trade policy, candidate type, ML, Holdings, or Screener integration was performed.',
        '',
        '## Coverage',
        '',
        f'- Stock latest date: {result.stock_latest_date or "missing"}',
        f'- Fetched stock tickers: {", ".join(result.fetched_stock_tickers) or "none"}',
        f'- Missing stock tickers: {", ".join(result.missing_stock_tickers) or "none"}',
        f'- Benchmark lag warning count: {sum(1 for row in result.alignments if row.benchmark_lag_warning)}',
        f'- Benchmark status counts: {dict(sorted(reason_counts.items()))}',
        '',
        '## Recommendation',
        '',
        f'- Recommendation: {result.recommendation.recommendation}',
        f'- Preferred benchmark: {result.recommendation.preferred_benchmark or "none"}',
        f'- Reason: {result.recommendation.reason}',
    ]
    return '\n'.join(lines) + '\n'


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = ['value']
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

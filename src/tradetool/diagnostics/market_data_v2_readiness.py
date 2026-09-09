from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from tradetool.data import PriceHistoryV2Record, load_price_history_v2_for_tickers
from tradetool.features import compute_raw_features


@dataclass(frozen=True, slots=True)
class V2PriceCoverageRow:
    ticker: str
    row_count: int
    min_price_date: str | None
    max_price_date: str | None
    enough_history_252: bool
    is_benchmark: bool
    present: bool

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'row_count': self.row_count,
            'min_price_date': self.min_price_date,
            'max_price_date': self.max_price_date,
            'enough_history_252': self.enough_history_252,
            'is_benchmark': self.is_benchmark,
            'present': self.present,
        }


@dataclass(frozen=True, slots=True)
class V2FeatureReadinessSampleRow:
    ticker: str
    feature_date: str
    feature_complete: bool
    feature_missing_reasons: tuple[str, ...]
    benchmark_ticker: str | None
    benchmark_alignment_status: str
    row_count: int
    latest_price_date: str
    ticker_latest_date: str
    benchmark_latest_date: str | None
    common_aligned_max_date: str | None
    benchmark_alignment_gap_days: int | None
    benchmark_lag_warning: bool
    aligned_row_count: int
    latest_close: float | int | bool | str | None
    raw_close_latest: float | None
    adjusted_close_latest: float | None
    close_input_source: str
    features: dict[str, float | int | bool | str | None]

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'feature_date': self.feature_date,
            'feature_complete': self.feature_complete,
            'feature_missing_reasons': '|'.join(self.feature_missing_reasons),
            'benchmark_ticker': self.benchmark_ticker or '',
            'benchmark_alignment_status': self.benchmark_alignment_status,
            'row_count': self.row_count,
            'latest_price_date': self.latest_price_date,
            'ticker_latest_date': self.ticker_latest_date,
            'benchmark_latest_date': self.benchmark_latest_date,
            'common_aligned_max_date': self.common_aligned_max_date,
            'benchmark_alignment_gap_days': self.benchmark_alignment_gap_days,
            'benchmark_lag_warning': self.benchmark_lag_warning,
            'aligned_row_count': self.aligned_row_count,
            'latest_close': self.latest_close,
            'raw_close_latest': self.raw_close_latest,
            'adjusted_close_latest': self.adjusted_close_latest,
            'close_input_source': self.close_input_source,
            **self.features,
        }


@dataclass(frozen=True, slots=True)
class MarketDataV2ReadinessResult:
    db_path: Path
    data_source: str
    requested_tickers: tuple[str, ...]
    benchmark_ticker: str | None
    coverage_rows: tuple[V2PriceCoverageRow, ...]
    feature_rows: tuple[V2FeatureReadinessSampleRow, ...]
    benchmark_present: bool
    benchmark_row_count: int
    benchmark_latest_date: str | None
    benchmark_lag_warning_count: int
    enough_history_count: int
    insufficient_history_count: int
    feature_complete_count: int
    feature_incomplete_count: int
    missing_reason_counts: dict[str, int]
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'db_path': str(self.db_path),
            'data_source': self.data_source,
            'requested_tickers': list(self.requested_tickers),
            'benchmark_ticker': self.benchmark_ticker,
            'benchmark_present': self.benchmark_present,
            'benchmark_row_count': self.benchmark_row_count,
            'benchmark_latest_date': self.benchmark_latest_date,
            'benchmark_lag_warning_count': self.benchmark_lag_warning_count,
            'coverage_count': len([row for row in self.coverage_rows if row.present and not row.is_benchmark]),
            'enough_history_count': self.enough_history_count,
            'insufficient_history_count': self.insufficient_history_count,
            'feature_complete_count': self.feature_complete_count,
            'feature_incomplete_count': self.feature_incomplete_count,
            'feature_missing_reason_counts': dict(self.missing_reason_counts),
            'generated_at_utc': self.generated_at_utc,
        }


def build_market_data_v2_readiness(
    *,
    db_path: str | Path,
    tickers: list[str],
    benchmark_ticker: str | None = None,
    data_source: str = 'yahoo',
    min_history_rows: int = 252,
) -> MarketDataV2ReadinessResult:
    requested_tickers = tuple(sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()}))
    all_tickers = list(requested_tickers)
    if benchmark_ticker:
        all_tickers.append(benchmark_ticker.strip().upper())
    loaded = load_price_history_v2_for_tickers(
        db_path=str(db_path),
        tickers=all_tickers,
        data_source=data_source,
    )

    benchmark_key = None if benchmark_ticker is None else benchmark_ticker.strip().upper()
    coverage_rows = tuple(
        _build_coverage_rows(
            requested_tickers=requested_tickers,
            benchmark_ticker=benchmark_key,
            rows_by_ticker=loaded.rows_by_ticker,
            min_history_rows=min_history_rows,
        )
    )
    feature_rows: list[V2FeatureReadinessSampleRow] = []
    reason_counter: Counter[str] = Counter()
    feature_rows_by_ticker = loaded.as_feature_rows_by_ticker()
    benchmark_v2_rows = loaded.rows_by_ticker.get(benchmark_key, ()) if benchmark_key else ()
    for ticker in requested_tickers:
        v2_rows = loaded.rows_by_ticker.get(ticker, ())
        if not v2_rows:
            continue
        alignment = _build_alignment_context(
            ticker_rows=v2_rows,
            benchmark_rows=benchmark_v2_rows,
            benchmark_ticker=benchmark_key,
            min_history_rows=min_history_rows,
        )
        feature_input_rows = tuple(row.to_feature_record() for row in alignment.ticker_rows_for_features)
        benchmark_input_rows = None
        if benchmark_key is not None and alignment.benchmark_rows_for_features:
            benchmark_input_rows = tuple(row.to_feature_record() for row in alignment.benchmark_rows_for_features)

        if feature_input_rows and (benchmark_key is None or benchmark_input_rows):
            feature_result = compute_raw_features(
                feature_input_rows,
                benchmark_rows=benchmark_input_rows,
                benchmark_ticker=benchmark_key,
            )
            features = dict(feature_result.features)
            feature_date = feature_result.feature_date.isoformat()
            feature_complete = feature_result.feature_complete and alignment.enough_aligned_history
            missing_reasons = tuple(dict.fromkeys((*feature_result.feature_missing_reasons, *alignment.missing_reasons)))
            latest_close = feature_result.features.get('latest_close')
            benchmark_alignment_status = feature_result.benchmark_alignment_status
        else:
            latest_v2_row_for_empty = v2_rows[-1]
            features = {
                'latest_close': latest_v2_row_for_empty.adjusted_close,
                'latest_price_date': alignment.feature_date.isoformat() if alignment.feature_date is not None else latest_v2_row_for_empty.price_date.isoformat(),
                'row_count': alignment.aligned_row_count,
            }
            feature_date = str(features['latest_price_date'])
            feature_complete = False
            missing_reasons = alignment.missing_reasons
            latest_close = features['latest_close']
            benchmark_alignment_status = alignment.status
        if not alignment.enough_aligned_history:
            feature_complete = False
        reason_counter.update(missing_reasons)
        latest_v2_row = alignment.latest_feature_row or v2_rows[-1]
        sample = V2FeatureReadinessSampleRow(
            ticker=ticker,
            feature_date=feature_date,
            feature_complete=feature_complete,
            feature_missing_reasons=missing_reasons,
            benchmark_ticker=benchmark_key,
            benchmark_alignment_status=benchmark_alignment_status if benchmark_alignment_status != 'aligned' or not alignment.benchmark_lag_warning else 'aligned_with_benchmark_lag',
            row_count=len(v2_rows),
            latest_price_date=latest_v2_row.price_date.isoformat(),
            ticker_latest_date=v2_rows[-1].price_date.isoformat(),
            benchmark_latest_date=None if not benchmark_v2_rows else benchmark_v2_rows[-1].price_date.isoformat(),
            common_aligned_max_date=None if alignment.common_aligned_max_date is None else alignment.common_aligned_max_date.isoformat(),
            benchmark_alignment_gap_days=alignment.benchmark_alignment_gap_days,
            benchmark_lag_warning=alignment.benchmark_lag_warning,
            aligned_row_count=alignment.aligned_row_count,
            latest_close=latest_close,
            raw_close_latest=latest_v2_row.raw_close,
            adjusted_close_latest=latest_v2_row.adjusted_close,
            close_input_source='adjusted_close',
            features=features,
        )
        feature_rows.append(sample)

    enough_history_count = sum(
        1 for row in coverage_rows if not row.is_benchmark and row.present and row.enough_history_252
    )
    feature_complete_count = sum(1 for row in feature_rows if row.feature_complete)
    return MarketDataV2ReadinessResult(
        db_path=Path(db_path).expanduser().resolve(),
        data_source=data_source,
        requested_tickers=requested_tickers,
        benchmark_ticker=benchmark_key,
        coverage_rows=coverage_rows,
        feature_rows=tuple(feature_rows),
        benchmark_present=bool(benchmark_v2_rows) if benchmark_key else False,
        benchmark_row_count=len(benchmark_v2_rows),
        benchmark_latest_date=None if not benchmark_v2_rows else benchmark_v2_rows[-1].price_date.isoformat(),
        benchmark_lag_warning_count=sum(1 for row in feature_rows if row.benchmark_lag_warning),
        enough_history_count=enough_history_count,
        insufficient_history_count=len(requested_tickers) - enough_history_count,
        feature_complete_count=feature_complete_count,
        feature_incomplete_count=len(feature_rows) - feature_complete_count,
        missing_reason_counts=dict(sorted(reason_counter.items())),
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_market_data_v2_readiness_outputs(*, result: MarketDataV2ReadinessResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'market_data_v2_readiness_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'market_data_v2_readiness_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')
    _write_csv(out_dir / 'v2_price_coverage.csv', [row.to_dict() for row in result.coverage_rows])
    _write_csv(out_dir / 'v2_feature_readiness_sample.csv', [row.to_dict() for row in result.feature_rows])


def _build_coverage_rows(
    *,
    requested_tickers: tuple[str, ...],
    benchmark_ticker: str | None,
    rows_by_ticker: dict[str, tuple[PriceHistoryV2Record, ...]] | object,
    min_history_rows: int,
):
    keys = list(requested_tickers)
    if benchmark_ticker is not None:
        keys.append(benchmark_ticker)
    for ticker in keys:
        rows = rows_by_ticker.get(ticker, ())  # type: ignore[attr-defined]
        present = bool(rows)
        yield V2PriceCoverageRow(
            ticker=ticker,
            row_count=len(rows),
            min_price_date=None if not rows else rows[0].price_date.isoformat(),
            max_price_date=None if not rows else rows[-1].price_date.isoformat(),
            enough_history_252=len(rows) >= min_history_rows,
            is_benchmark=ticker == benchmark_ticker,
            present=present,
        )


@dataclass(frozen=True, slots=True)
class _AlignmentContext:
    status: str
    common_aligned_max_date: date | None
    benchmark_alignment_gap_days: int | None
    benchmark_lag_warning: bool
    aligned_row_count: int
    enough_aligned_history: bool
    missing_reasons: tuple[str, ...]
    ticker_rows_for_features: tuple[PriceHistoryV2Record, ...]
    benchmark_rows_for_features: tuple[PriceHistoryV2Record, ...]
    latest_feature_row: PriceHistoryV2Record | None
    feature_date: date | None


def _build_alignment_context(
    *,
    ticker_rows: tuple[PriceHistoryV2Record, ...],
    benchmark_rows: tuple[PriceHistoryV2Record, ...],
    benchmark_ticker: str | None,
    min_history_rows: int = 252,
) -> _AlignmentContext:
    ticker_latest_date = ticker_rows[-1].price_date
    if benchmark_ticker is None:
        enough = len(ticker_rows) >= min_history_rows
        return _AlignmentContext(
            status='not_requested',
            common_aligned_max_date=ticker_latest_date,
            benchmark_alignment_gap_days=None,
            benchmark_lag_warning=False,
            aligned_row_count=len(ticker_rows),
            enough_aligned_history=enough,
            missing_reasons=() if enough else ('insufficient_rows_for_12m_return',),
            ticker_rows_for_features=ticker_rows,
            benchmark_rows_for_features=(),
            latest_feature_row=ticker_rows[-1],
            feature_date=ticker_latest_date,
        )
    if not benchmark_rows:
        return _AlignmentContext(
            status='benchmark_missing',
            common_aligned_max_date=None,
            benchmark_alignment_gap_days=None,
            benchmark_lag_warning=False,
            aligned_row_count=0,
            enough_aligned_history=False,
            missing_reasons=('missing_benchmark_data',),
            ticker_rows_for_features=(),
            benchmark_rows_for_features=(),
            latest_feature_row=None,
            feature_date=ticker_latest_date,
        )

    benchmark_latest_date = benchmark_rows[-1].price_date
    common_dates = {row.price_date for row in ticker_rows}.intersection(row.price_date for row in benchmark_rows)
    if not common_dates:
        return _AlignmentContext(
            status='alignment_failed',
            common_aligned_max_date=None,
            benchmark_alignment_gap_days=(ticker_latest_date - benchmark_latest_date).days,
            benchmark_lag_warning=benchmark_latest_date < ticker_latest_date,
            aligned_row_count=0,
            enough_aligned_history=False,
            missing_reasons=('benchmark_alignment_failed',),
            ticker_rows_for_features=(),
            benchmark_rows_for_features=(),
            latest_feature_row=None,
            feature_date=ticker_latest_date,
        )

    common_aligned_max_date = max(common_dates)
    ticker_rows_for_features = tuple(row for row in ticker_rows if row.price_date <= common_aligned_max_date)
    benchmark_rows_for_features = tuple(row for row in benchmark_rows if row.price_date <= common_aligned_max_date)
    aligned_row_count = min(len(ticker_rows_for_features), len(benchmark_rows_for_features))
    enough_aligned_history = aligned_row_count >= min_history_rows
    missing_reasons = () if enough_aligned_history else ('insufficient_aligned_rows_for_12m_return',)
    return _AlignmentContext(
        status='aligned',
        common_aligned_max_date=common_aligned_max_date,
        benchmark_alignment_gap_days=(ticker_latest_date - benchmark_latest_date).days,
        benchmark_lag_warning=benchmark_latest_date < ticker_latest_date,
        aligned_row_count=aligned_row_count,
        enough_aligned_history=enough_aligned_history,
        missing_reasons=missing_reasons,
        ticker_rows_for_features=ticker_rows_for_features,
        benchmark_rows_for_features=benchmark_rows_for_features,
        latest_feature_row=ticker_rows_for_features[-1],
        feature_date=common_aligned_max_date,
    )


def _render_summary_markdown(result: MarketDataV2ReadinessResult) -> str:
    lines = [
        '# Market Data V2 Readiness Summary',
        '',
        '## Scope',
        '',
        f'- DB path: {result.db_path}',
        f'- Data source: {result.data_source}',
        f'- Requested tickers: {", ".join(result.requested_tickers) or "none"}',
        f'- Benchmark ticker: {result.benchmark_ticker or "not requested"}',
        '',
        '## Coverage',
        '',
        f'- Benchmark present: {result.benchmark_present}',
        f'- Benchmark row count: {result.benchmark_row_count}',
        f'- Benchmark latest date: {result.benchmark_latest_date or "missing"}',
        f'- Benchmark lag warning count: {result.benchmark_lag_warning_count}',
        f'- Enough-history count (>=252 rows): {result.enough_history_count}',
        f'- Insufficient-history count: {result.insufficient_history_count}',
        '',
        '## Feature readiness',
        '',
        f'- Feature complete count: {result.feature_complete_count}',
        f'- Feature incomplete count: {result.feature_incomplete_count}',
        '- V2 feature close uses adjusted_close; raw_close is preserved separately for diagnostics.',
        '- No ranking, trade policy, candidate type, ML, Holdings, or DB writes were performed.',
        '',
        '## Missing reason counts',
        '',
    ]
    if result.missing_reason_counts:
        for reason, count in result.missing_reason_counts.items():
            lines.append(f'- {reason}: {count}')
    else:
        lines.append('- none')
    return '\n'.join(lines) + '\n'


def _write_csv(path: Path, rows) -> None:
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

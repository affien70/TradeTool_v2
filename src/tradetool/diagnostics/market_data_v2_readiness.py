from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
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
    if benchmark_key is not None and benchmark_key not in loaded.rows_by_ticker:
        raise ValueError(f'Benchmark ticker "{benchmark_key}" is missing from price_history_v2 for data source "{data_source}".')

    feature_rows: list[V2FeatureReadinessSampleRow] = []
    reason_counter: Counter[str] = Counter()
    feature_rows_by_ticker = loaded.as_feature_rows_by_ticker()
    for ticker in requested_tickers:
        v2_rows = loaded.rows_by_ticker.get(ticker, ())
        if not v2_rows:
            continue
        feature_result = compute_raw_features(
            feature_rows_by_ticker[ticker],
            benchmark_rows=None if benchmark_key is None else feature_rows_by_ticker.get(benchmark_key),
            benchmark_ticker=benchmark_key,
        )
        latest_v2_row = v2_rows[-1]
        sample = V2FeatureReadinessSampleRow(
            ticker=ticker,
            feature_date=feature_result.feature_date.isoformat(),
            feature_complete=feature_result.feature_complete,
            feature_missing_reasons=feature_result.feature_missing_reasons,
            benchmark_ticker=feature_result.benchmark_ticker,
            benchmark_alignment_status=feature_result.benchmark_alignment_status,
            row_count=len(v2_rows),
            latest_price_date=latest_v2_row.price_date.isoformat(),
            latest_close=feature_result.features.get('latest_close'),
            raw_close_latest=latest_v2_row.raw_close,
            adjusted_close_latest=latest_v2_row.adjusted_close,
            close_input_source='adjusted_close',
            features=dict(feature_result.features),
        )
        feature_rows.append(sample)
        reason_counter.update(feature_result.feature_missing_reasons)

    enough_history_count = sum(
        1 for row in coverage_rows if not row.is_benchmark and row.present and row.enough_history_252
    )
    feature_complete_count = sum(1 for row in feature_rows if row.feature_complete)
    benchmark_rows = loaded.rows_by_ticker.get(benchmark_key, ()) if benchmark_key else ()
    return MarketDataV2ReadinessResult(
        db_path=Path(db_path).expanduser().resolve(),
        data_source=data_source,
        requested_tickers=requested_tickers,
        benchmark_ticker=benchmark_key,
        coverage_rows=coverage_rows,
        feature_rows=tuple(feature_rows),
        benchmark_present=bool(benchmark_rows) if benchmark_key else False,
        benchmark_row_count=len(benchmark_rows),
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

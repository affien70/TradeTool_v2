from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from tradetool.data.market_data_schema import (
    MarketDataDryRunResult,
    initialize_market_data_schema,
    inspect_market_data_schema,
)
from tradetool.data.market_data_source import (
    MarketDataSource,
    MarketDataSourceDependencyError,
    MarketDataSourceFetchResult,
    TickerFetchStatus,
    build_market_data_source,
    normalize_source_rows_for_v2,
)


@dataclass(frozen=True, slots=True)
class NormalizedRowSample:
    ticker: str
    price_date: str
    raw_open: float | None
    raw_high: float | None
    raw_low: float | None
    raw_close: float | None
    adjusted_close: float | None
    volume: float | None
    data_source: str
    warning_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'price_date': self.price_date,
            'raw_open': self.raw_open,
            'raw_high': self.raw_high,
            'raw_low': self.raw_low,
            'raw_close': self.raw_close,
            'adjusted_close': self.adjusted_close,
            'volume': self.volume,
            'data_source': self.data_source,
            'warning_codes': '; '.join(self.warning_codes),
        }


@dataclass(frozen=True, slots=True)
class MarketDataFetchDryRunResult:
    db_path: Path
    source_name: str
    requested_tickers: tuple[str, ...]
    fetched_tickers: tuple[str, ...]
    missing_tickers: tuple[str, ...]
    provider_warning: str | None
    provider_error: str | None
    schema_row_count: int
    dry_run: MarketDataDryRunResult
    ticker_statuses: tuple[TickerFetchStatus, ...]
    normalized_rows: tuple[NormalizedRowSample, ...]
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'db_path': str(self.db_path),
            'source_name': self.source_name,
            'requested_tickers': list(self.requested_tickers),
            'fetched_tickers': list(self.fetched_tickers),
            'missing_tickers': list(self.missing_tickers),
            'provider_warning': self.provider_warning,
            'provider_error': self.provider_error,
            'schema_row_count': self.schema_row_count,
            'valid_normalized_rows': self.dry_run.valid_row_count,
            'invalid_normalized_rows': self.dry_run.invalid_row_count,
            'invalid_reasons': dict(self.dry_run.invalid_reasons),
            'would_insert': self.dry_run.would_insert,
            'would_update': self.dry_run.would_update,
            'would_skip': self.dry_run.would_skip,
            'generated_at_utc': self.generated_at_utc,
        }


def build_market_data_fetch_dry_run(
    *,
    db_path: str | Path,
    tickers: list[str],
    start_date: date,
    end_date: date,
    source_name: str,
    source_override: MarketDataSource | None = None,
) -> MarketDataFetchDryRunResult:
    schema = _ensure_schema_exists(db_path)
    generated_at_utc = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        source = source_override or build_market_data_source(source_name)
        fetch_result = source.fetch_daily_rows(
            tickers=tickers,
            start_date=start_date,
            end_date=end_date,
        )
        provider_error = None
        provider_warning = fetch_result.provider_warning
    except MarketDataSourceDependencyError as exc:
        fetch_result = MarketDataSourceFetchResult(
            source_name=source_name,
            requested_tickers=tuple(ticker.strip().upper() for ticker in tickers if ticker.strip()),
            rows=(),
            ticker_statuses=tuple(
                TickerFetchStatus(
                    ticker=ticker.strip().upper(),
                    fetched=False,
                    row_count=0,
                    status='provider_unavailable',
                    message=str(exc),
                )
                for ticker in tickers
                if ticker.strip()
            ),
            provider_error=str(exc),
        )
        provider_error = str(exc)
        provider_warning = None

    normalized_rows = tuple(
        NormalizedRowSample(
            ticker=row.ticker,
            price_date=row.price_date,
            raw_open=row.raw_open,
            raw_high=row.raw_high,
            raw_low=row.raw_low,
            raw_close=row.raw_close,
            adjusted_close=row.adjusted_close if row.adjusted_close is not None else row.raw_close,
            volume=row.volume,
            data_source=row.data_source,
            warning_codes=row.warning_codes,
        )
        for row in fetch_result.rows
    )
    market_rows = normalize_source_rows_for_v2(
        rows=fetch_result.rows,
        created_at_utc=generated_at_utc,
        updated_at_utc=generated_at_utc,
    )
    dry_run = _dry_run_market_rows(db_path=db_path, market_rows=market_rows)
    fetched_tickers = tuple(sorted({status.ticker for status in fetch_result.ticker_statuses if status.fetched}))
    missing_tickers = tuple(sorted(status.ticker for status in fetch_result.ticker_statuses if not status.fetched))
    return MarketDataFetchDryRunResult(
        db_path=schema.db_path,
        source_name=source_name,
        requested_tickers=fetch_result.requested_tickers,
        fetched_tickers=fetched_tickers,
        missing_tickers=missing_tickers,
        provider_warning=provider_warning,
        provider_error=provider_error,
        schema_row_count=schema.row_count,
        dry_run=dry_run,
        ticker_statuses=fetch_result.ticker_statuses,
        normalized_rows=normalized_rows,
        generated_at_utc=generated_at_utc,
    )


def write_market_data_fetch_dry_run_outputs(*, result: MarketDataFetchDryRunResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'market_data_fetch_dry_run_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'market_data_fetch_dry_run_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')
    _write_csv(out_dir / 'normalized_rows_sample.csv', [row.to_dict() for row in result.normalized_rows[:500]])
    _write_csv(
        out_dir / 'invalid_rows.csv',
        [row.to_dict() for row in result.dry_run.rows if not row.valid],
    )
    _write_csv(out_dir / 'ticker_fetch_status.csv', [row.to_dict() for row in result.ticker_statuses])


def _ensure_schema_exists(db_path: str | Path):
    try:
        inspection = inspect_market_data_schema(db_path)
        if inspection.schema_initialized:
            return inspection
    except FileNotFoundError:
        pass
    return initialize_market_data_schema(db_path)


def _dry_run_market_rows(*, db_path: str | Path, market_rows):
    from tradetool.data.market_data_schema import dry_run_market_data_rows

    return dry_run_market_data_rows(db_path=db_path, rows=market_rows)


def _render_summary_markdown(result: MarketDataFetchDryRunResult) -> str:
    lines = [
        '# Market Data Fetch Dry Run Summary',
        '',
        '## Scope',
        '',
        f'- DB path: {result.db_path}',
        f'- Source: {result.source_name}',
        f'- Requested tickers: {", ".join(result.requested_tickers) or "none"}',
        f'- Fetched tickers: {", ".join(result.fetched_tickers) or "none"}',
        f'- Missing tickers: {", ".join(result.missing_tickers) or "none"}',
        f'- Schema row count before/after dry run: {result.schema_row_count}',
        '',
        '## Provider',
        '',
        f'- Provider warning: {result.provider_warning or "none"}',
        f'- Provider error: {result.provider_error or "none"}',
        '',
        '## Dry-run validation',
        '',
        f'- Valid normalized rows: {result.dry_run.valid_row_count}',
        f'- Invalid normalized rows: {result.dry_run.invalid_row_count}',
        f'- would_insert: {result.dry_run.would_insert}',
        f'- would_update: {result.dry_run.would_update}',
        f'- would_skip: {result.dry_run.would_skip}',
        '- No database rows were written.',
        '- No legacy database access or writes were performed.',
        '',
        '## Invalid reasons',
        '',
    ]
    if result.dry_run.invalid_reasons:
        for reason, count in result.dry_run.invalid_reasons.items():
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

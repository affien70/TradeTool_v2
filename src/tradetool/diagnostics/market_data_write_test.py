from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from tradetool.data.market_data_schema import (
    MarketDataDryRunValidationRow,
    MarketDataWriteResult,
    initialize_market_data_schema,
    write_market_data_rows_for_test,
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
class MarketDataWriteRowSample:
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
    validation_warnings: tuple[str, ...]
    action: str
    reasons: tuple[str, ...]

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
            'validation_warnings': '; '.join(self.validation_warnings),
            'action': self.action,
            'reasons': '; '.join(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class MarketDataWriteTestResult:
    db_path: Path
    source_name: str
    requested_tickers: tuple[str, ...]
    fetched_tickers: tuple[str, ...]
    missing_tickers: tuple[str, ...]
    provider_warning: str | None
    provider_error: str | None
    write_result: MarketDataWriteResult
    ticker_statuses: tuple[TickerFetchStatus, ...]
    written_rows: tuple[MarketDataWriteRowSample, ...]
    invalid_rows: tuple[MarketDataWriteRowSample, ...]
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
            'row_count_before': self.write_result.row_count_before,
            'row_count_after': self.write_result.row_count_after,
            'inserted_count': self.write_result.inserted_count,
            'updated_count': self.write_result.updated_count,
            'skipped_count': self.write_result.skipped_count,
            'invalid_row_count': self.write_result.invalid_row_count,
            'invalid_reasons': dict(self.write_result.invalid_reasons),
            'tolerated_warning_count': sum(self.write_result.tolerated_warnings.values()),
            'tolerated_warnings': dict(self.write_result.tolerated_warnings),
            'generated_at_utc': self.generated_at_utc,
        }


def build_market_data_write_test_result(
    *,
    db_path: str | Path,
    tickers: list[str],
    start_date: date,
    end_date: date,
    source_name: str,
    allow_test_db_write: bool,
    source_override: MarketDataSource | None = None,
) -> MarketDataWriteTestResult:
    if not allow_test_db_write:
        raise ValueError('Explicit --allow-test-db-write approval is required for temporary market-data writes.')
    schema = initialize_market_data_schema(db_path)
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

    market_rows = normalize_source_rows_for_v2(
        rows=fetch_result.rows,
        created_at_utc=generated_at_utc,
        updated_at_utc=generated_at_utc,
    )
    write_result = write_market_data_rows_for_test(
        db_path=schema.db_path,
        rows=market_rows,
        allow_test_db_write=allow_test_db_write,
    )
    action_rows = _build_action_rows(
        fetch_rows=fetch_result.rows,
        validation_rows=write_result.rows,
    )
    fetched_tickers = tuple(sorted({status.ticker for status in fetch_result.ticker_statuses if status.fetched}))
    missing_tickers = tuple(sorted(status.ticker for status in fetch_result.ticker_statuses if not status.fetched))
    return MarketDataWriteTestResult(
        db_path=schema.db_path,
        source_name=source_name,
        requested_tickers=fetch_result.requested_tickers,
        fetched_tickers=fetched_tickers,
        missing_tickers=missing_tickers,
        provider_warning=provider_warning,
        provider_error=provider_error,
        write_result=write_result,
        ticker_statuses=fetch_result.ticker_statuses,
        written_rows=tuple(row for row in action_rows if row.action in {'inserted', 'updated'}),
        invalid_rows=tuple(row for row in action_rows if row.action == 'invalid'),
        generated_at_utc=generated_at_utc,
    )


def write_market_data_write_test_outputs(*, result: MarketDataWriteTestResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'market_data_write_test_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'market_data_write_test_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')
    _write_csv(out_dir / 'written_rows_sample.csv', [row.to_dict() for row in result.written_rows[:500]])
    _write_csv(out_dir / 'invalid_rows.csv', [row.to_dict() for row in result.invalid_rows[:500]])
    _write_csv(out_dir / 'ticker_fetch_status.csv', [row.to_dict() for row in result.ticker_statuses])


def _build_action_rows(
    *,
    fetch_rows,
    validation_rows: tuple[MarketDataDryRunValidationRow, ...],
) -> tuple[MarketDataWriteRowSample, ...]:
    action_rows: list[MarketDataWriteRowSample] = []
    for fetch_row, validation_row in zip(fetch_rows, validation_rows):
        action_rows.append(
            MarketDataWriteRowSample(
                ticker=fetch_row.ticker,
                price_date=fetch_row.price_date,
                raw_open=fetch_row.raw_open,
                raw_high=fetch_row.raw_high,
                raw_low=fetch_row.raw_low,
                raw_close=fetch_row.raw_close,
                adjusted_close=fetch_row.adjusted_close if fetch_row.adjusted_close is not None else fetch_row.raw_close,
                volume=fetch_row.volume,
                data_source=fetch_row.data_source,
                warning_codes=fetch_row.warning_codes,
                validation_warnings=validation_row.validation_warnings,
                action=_normalize_action(validation_row.action),
                reasons=validation_row.reasons,
            )
        )
    return tuple(action_rows)


def _normalize_action(action: str) -> str:
    return {
        'would_insert': 'inserted',
        'would_update': 'updated',
        'would_skip': 'skipped',
        'invalid': 'invalid',
    }.get(action, action)


def _render_summary_markdown(result: MarketDataWriteTestResult) -> str:
    lines = [
        '# Market Data Write Test Summary',
        '',
        '## Scope',
        '',
        f'- DB path: {result.db_path}',
        f'- Source: {result.source_name}',
        f'- Requested tickers: {", ".join(result.requested_tickers) or "none"}',
        f'- Fetched tickers: {", ".join(result.fetched_tickers) or "none"}',
        f'- Missing tickers: {", ".join(result.missing_tickers) or "none"}',
        '',
        '## Provider',
        '',
        f'- Provider warning: {result.provider_warning or "none"}',
        f'- Provider error: {result.provider_error or "none"}',
        '',
        '## Write result',
        '',
        f'- row_count_before: {result.write_result.row_count_before}',
        f'- row_count_after: {result.write_result.row_count_after}',
        f'- inserted_count: {result.write_result.inserted_count}',
        f'- updated_count: {result.write_result.updated_count}',
        f'- skipped_count: {result.write_result.skipped_count}',
        f'- invalid_row_count: {result.write_result.invalid_row_count}',
        f'- tolerated_warning_count: {sum(result.write_result.tolerated_warnings.values())}',
        '- Writes are limited to an explicit temporary test DB.',
        '- No legacy database access or writes were performed.',
        '',
        '## Invalid reasons',
        '',
    ]
    if result.write_result.invalid_reasons:
        for reason, count in result.write_result.invalid_reasons.items():
            lines.append(f'- {reason}: {count}')
    else:
        lines.append('- none')
    lines.extend(
        [
            '',
            '## Tolerated warnings',
            '',
        ]
    )
    if result.write_result.tolerated_warnings:
        for reason, count in result.write_result.tolerated_warnings.items():
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

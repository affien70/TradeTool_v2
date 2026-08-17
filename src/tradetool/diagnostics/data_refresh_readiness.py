from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from tradetool.data import ReadOnlySQLite
from tradetool.diagnostics.coverage import _coerce_optional_date, _resolve_column_name, _resolve_price_table
from tradetool.diagnostics.eligibility import REQUIRED_OHLCV_COLUMNS, build_eligibility_diagnostics
from tradetool.universe import load_universe_tickers

LEGACY_PRODUCTION_DB_PATH = Path('/Users/affien/DEV/TradeTool/portfolio.sqlite').resolve()
DECISION_READY = 'ready_for_refresh_design'
DECISION_BLOCKED_SCHEMA = 'blocked_schema_unknown'
DECISION_BLOCKED_QUALITY = 'blocked_data_quality_issue'
DECISION_BLOCKED_UNSAFE = 'blocked_unsafe_path'

_DATE_COLUMN_CANDIDATES = ('date', 'trade_date', 'price_date', 'as_of_date')
_REQUIRED_PRICE_COLUMNS = ('ticker', 'open', 'high', 'low', 'close', 'volume')


@dataclass(frozen=True, slots=True)
class PriceDateCoverageRow:
    latest_price_date: str
    ticker_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            'latest_price_date': self.latest_price_date,
            'ticker_count': self.ticker_count,
        }


@dataclass(frozen=True, slots=True)
class RefreshGapRow:
    ticker: str
    latest_price_date: str | None
    universe_max_price_date: str | None
    days_behind_universe_max: int | None
    days_behind_today: int | None
    row_count: int
    structurally_eligible: bool | None
    structural_rejection_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'latest_price_date': self.latest_price_date,
            'universe_max_price_date': self.universe_max_price_date,
            'days_behind_universe_max': self.days_behind_universe_max,
            'days_behind_today': self.days_behind_today,
            'row_count': self.row_count,
            'structurally_eligible': self.structurally_eligible,
            'structural_rejection_reasons': '; '.join(self.structural_rejection_reasons),
        }


@dataclass(frozen=True, slots=True)
class SchemaWriteRiskAuditRow:
    risk_name: str
    status: str
    note: str

    def to_dict(self) -> dict[str, object]:
        return {
            'risk_name': self.risk_name,
            'status': self.status,
            'note': self.note,
        }


@dataclass(frozen=True, slots=True)
class DataRefreshReadinessResult:
    db_path: Path
    read_only_open_status: bool
    unsafe_legacy_db_path: bool
    detected_price_table: str
    detected_universe_source: str
    universe_id: str
    universe_ticker_count: int
    benchmark_ticker: str | None
    price_table_columns: tuple[str, ...]
    price_date_column: str | None
    required_columns_present: tuple[str, ...]
    required_columns_missing: tuple[str, ...]
    duplicate_ticker_date_rows: int
    invalid_ohlc_row_count: int
    invalid_ohlc_ticker_count: int
    universe_max_price_date: str | None
    min_latest_ticker_date: str | None
    max_latest_ticker_date: str | None
    benchmark_latest_date: str | None
    benchmark_aligned_with_universe_max: bool | None
    stale_vs_universe_max_count: int
    stale_vs_today_count: int
    recommendation: str
    price_date_coverage_rows: tuple[PriceDateCoverageRow, ...]
    refresh_gap_rows: tuple[RefreshGapRow, ...]
    schema_write_risk_audit_rows: tuple[SchemaWriteRiskAuditRow, ...]
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'db_path': str(self.db_path),
            'read_only_open_status': self.read_only_open_status,
            'unsafe_legacy_db_path': self.unsafe_legacy_db_path,
            'detected_price_table': self.detected_price_table,
            'detected_universe_source': self.detected_universe_source,
            'universe_id': self.universe_id,
            'universe_ticker_count': self.universe_ticker_count,
            'benchmark_ticker': self.benchmark_ticker,
            'price_table_columns': list(self.price_table_columns),
            'price_date_column': self.price_date_column,
            'required_columns_present': list(self.required_columns_present),
            'required_columns_missing': list(self.required_columns_missing),
            'duplicate_ticker_date_rows': self.duplicate_ticker_date_rows,
            'invalid_ohlc_row_count': self.invalid_ohlc_row_count,
            'invalid_ohlc_ticker_count': self.invalid_ohlc_ticker_count,
            'universe_max_price_date': self.universe_max_price_date,
            'min_latest_ticker_date': self.min_latest_ticker_date,
            'max_latest_ticker_date': self.max_latest_ticker_date,
            'benchmark_latest_date': self.benchmark_latest_date,
            'benchmark_aligned_with_universe_max': self.benchmark_aligned_with_universe_max,
            'stale_vs_universe_max_count': self.stale_vs_universe_max_count,
            'stale_vs_today_count': self.stale_vs_today_count,
            'latest_price_date_distribution': [row.to_dict() for row in self.price_date_coverage_rows],
            'recommendation': self.recommendation,
            'generated_at_utc': self.generated_at_utc,
        }


def build_data_refresh_readiness_audit(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str | None = None,
    price_table: str | None = None,
    today: date | None = None,
    unsafe_legacy_db_path: Path = LEGACY_PRODUCTION_DB_PATH,
) -> DataRefreshReadinessResult:
    resolved_db_path = Path(db_path).expanduser().resolve()
    is_unsafe_path = resolved_db_path == unsafe_legacy_db_path.resolve()
    database = ReadOnlySQLite(resolved_db_path)
    with database.connect():
        read_only_open_status = True

    schema = database.inspect_schema()
    resolved_price_table = price_table or _resolve_price_table(schema)
    table_columns = tuple(column.name for column in schema.columns_by_table.get(resolved_price_table, ()))
    normalized_columns = {name.lower(): name for name in table_columns}
    required_present = tuple(column for column in _REQUIRED_PRICE_COLUMNS if column in normalized_columns)
    ticker_column = _resolve_column_name(schema, resolved_price_table, ('ticker', 'symbol', 'ric'), 'ticker')
    price_date_column = _resolve_optional_column_name(schema, resolved_price_table, _DATE_COLUMN_CANDIDATES)
    required_missing = tuple(
        list(column for column in _REQUIRED_PRICE_COLUMNS if column not in normalized_columns)
        + ([] if price_date_column is not None else ['date'])
    )
    universe = load_universe_tickers(
        universe_id=universe_id,
        database=database,
        price_table=resolved_price_table,
        ticker_column=ticker_column,
    )
    relevant_tickers = set(universe.tickers)
    if benchmark_ticker:
        relevant_tickers.add(benchmark_ticker.upper())
    stats = _collect_refresh_statistics_without_date(
        database=database,
        table_name=resolved_price_table,
        ticker_column=ticker_column,
        relevant_tickers=relevant_tickers,
    )
    if price_date_column is not None:
        stats = _collect_refresh_statistics(
            database=database,
            table_name=resolved_price_table,
            ticker_column=ticker_column,
            date_column=price_date_column,
            relevant_tickers=relevant_tickers,
            has_all_ohlcv_columns=len(required_missing) == 0,
            open_column=normalized_columns.get('open'),
            high_column=normalized_columns.get('high'),
            low_column=normalized_columns.get('low'),
            close_column=normalized_columns.get('close'),
            volume_column=normalized_columns.get('volume'),
        )

    current_date = today or datetime.now(UTC).date()
    universe_latest_dates = {
        ticker: stats['latest_date_by_ticker'].get(ticker)
        for ticker in universe.tickers
    }
    present_latest_dates = [value for value in universe_latest_dates.values() if value is not None]
    universe_max_price_date = max(present_latest_dates) if present_latest_dates else None
    universe_min_price_date = min(present_latest_dates) if present_latest_dates else None
    benchmark_latest_date = stats['latest_date_by_ticker'].get(benchmark_ticker.upper()) if benchmark_ticker else None
    eligibility_map = _build_eligibility_map(
        db_path=resolved_db_path,
        universe_id=universe_id,
        price_table=resolved_price_table,
    ) if len(required_missing) == 0 else {}

    refresh_gap_rows = tuple(
        RefreshGapRow(
            ticker=ticker,
            latest_price_date=None if latest_date is None else latest_date.isoformat(),
            universe_max_price_date=None if universe_max_price_date is None else universe_max_price_date.isoformat(),
            days_behind_universe_max=None if latest_date is None or universe_max_price_date is None else (universe_max_price_date - latest_date).days,
            days_behind_today=None if latest_date is None else (current_date - latest_date).days,
            row_count=int(stats['rows_by_ticker'].get(ticker, 0)),
            structurally_eligible=None if ticker not in eligibility_map else eligibility_map[ticker][0],
            structural_rejection_reasons=() if ticker not in eligibility_map else eligibility_map[ticker][1],
        )
        for ticker, latest_date in sorted(universe_latest_dates.items())
    )
    stale_vs_universe_max_count = sum(1 for row in refresh_gap_rows if row.days_behind_universe_max not in (None, 0))
    stale_vs_today_count = sum(1 for row in refresh_gap_rows if row.days_behind_today not in (None, 0))
    price_date_coverage_rows = tuple(
        PriceDateCoverageRow(latest_price_date=price_date, ticker_count=count)
        for price_date, count in sorted(_latest_date_distribution_for_universe(universe_latest_dates).items())
    )
    benchmark_aligned = None if benchmark_ticker is None or universe_max_price_date is None or benchmark_latest_date is None else benchmark_latest_date == universe_max_price_date

    recommendation = _recommendation(
        is_unsafe_path=is_unsafe_path,
        required_missing=required_missing,
        duplicate_rows=stats['duplicate_ticker_date_rows'],
        invalid_ohlc_rows=stats['invalid_ohlc_row_count'],
    )
    risk_rows = _build_risk_audit_rows(is_unsafe_path=is_unsafe_path, benchmark_ticker=benchmark_ticker)

    return DataRefreshReadinessResult(
        db_path=resolved_db_path,
        read_only_open_status=read_only_open_status,
        unsafe_legacy_db_path=is_unsafe_path,
        detected_price_table=resolved_price_table,
        detected_universe_source=universe.source,
        universe_id=universe_id,
        universe_ticker_count=len(universe.tickers),
        benchmark_ticker=benchmark_ticker,
        price_table_columns=table_columns,
        price_date_column=price_date_column,
        required_columns_present=required_present,
        required_columns_missing=required_missing,
        duplicate_ticker_date_rows=int(stats['duplicate_ticker_date_rows']),
        invalid_ohlc_row_count=int(stats['invalid_ohlc_row_count']),
        invalid_ohlc_ticker_count=int(len(stats['invalid_ohlc_tickers'])),
        universe_max_price_date=None if universe_max_price_date is None else universe_max_price_date.isoformat(),
        min_latest_ticker_date=None if universe_min_price_date is None else universe_min_price_date.isoformat(),
        max_latest_ticker_date=None if universe_max_price_date is None else universe_max_price_date.isoformat(),
        benchmark_latest_date=None if benchmark_latest_date is None else benchmark_latest_date.isoformat(),
        benchmark_aligned_with_universe_max=benchmark_aligned,
        stale_vs_universe_max_count=stale_vs_universe_max_count,
        stale_vs_today_count=stale_vs_today_count,
        recommendation=recommendation,
        price_date_coverage_rows=price_date_coverage_rows,
        refresh_gap_rows=refresh_gap_rows,
        schema_write_risk_audit_rows=risk_rows,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_data_refresh_readiness_outputs(*, result: DataRefreshReadinessResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / 'price_date_coverage.csv', [row.to_dict() for row in result.price_date_coverage_rows])
    _write_csv(out_dir / 'refresh_gap_by_ticker.csv', [row.to_dict() for row in result.refresh_gap_rows])
    _write_csv(out_dir / 'schema_write_risk_audit.csv', [row.to_dict() for row in result.schema_write_risk_audit_rows])
    (out_dir / 'data_refresh_readiness_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'data_refresh_readiness_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')


def _build_eligibility_map(*, db_path: Path, universe_id: str, price_table: str) -> dict[str, tuple[bool, tuple[str, ...]]]:
    eligibility = build_eligibility_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        price_table=price_table,
    )
    return {
        row.ticker: (row.eligible, row.rejection_reasons)
        for row in eligibility.results
        if not row.ticker.startswith('<INVALID_TICKER_')
    }


def _latest_date_distribution_for_universe(latest_date_by_ticker: Mapping[str, date | None]) -> Counter[str]:
    distribution: Counter[str] = Counter()
    for latest_date in latest_date_by_ticker.values():
        if latest_date is not None:
            distribution[latest_date.isoformat()] += 1
    return distribution


def _resolve_optional_column_name(schema, table_name: str, candidates: Sequence[str]) -> str | None:
    columns = schema.columns_by_table.get(table_name)
    if columns is None:
        return None
    normalized = {column.name.lower(): column.name for column in columns}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def _collect_refresh_statistics_without_date(
    *,
    database: ReadOnlySQLite,
    table_name: str,
    ticker_column: str,
    relevant_tickers: set[str],
) -> dict[str, object]:
    quoted_ticker = f'"{ticker_column}"'
    scope_filter_sql, scope_parameters = _scope_filter_clause(quoted_ticker, relevant_tickers)
    rows_by_ticker_rows = database.fetch_all(
        f"""
        SELECT UPPER(TRIM({quoted_ticker})) AS ticker, COUNT(*) AS row_count
        FROM "{table_name}"
        WHERE {quoted_ticker} IS NOT NULL AND TRIM({quoted_ticker}) <> ''{scope_filter_sql}
        GROUP BY UPPER(TRIM({quoted_ticker}))
        """,
        scope_parameters,
    )
    return {
        'rows_by_ticker': {str(row['ticker']): int(row['row_count']) for row in rows_by_ticker_rows},
        'latest_date_by_ticker': {},
        'duplicate_ticker_date_rows': 0,
        'invalid_ohlc_row_count': 0,
        'invalid_ohlc_tickers': set(),
        'min_price_date': None,
        'max_price_date': None,
    }


def _collect_refresh_statistics(
    *,
    database: ReadOnlySQLite,
    table_name: str,
    ticker_column: str,
    date_column: str,
    relevant_tickers: set[str],
    has_all_ohlcv_columns: bool,
    open_column: str | None,
    high_column: str | None,
    low_column: str | None,
    close_column: str | None,
    volume_column: str | None,
) -> dict[str, object]:
    quoted_ticker = f'"{ticker_column}"'
    quoted_date = f'"{date_column}"'
    scope_filter_sql, scope_parameters = _scope_filter_clause(quoted_ticker, relevant_tickers)
    rows_by_ticker_rows = database.fetch_all(
        f"""
        SELECT UPPER(TRIM({quoted_ticker})) AS ticker, COUNT(*) AS row_count
        FROM "{table_name}"
        WHERE {quoted_ticker} IS NOT NULL AND TRIM({quoted_ticker}) <> ''{scope_filter_sql}
        GROUP BY UPPER(TRIM({quoted_ticker}))
        """,
        scope_parameters,
    )
    latest_by_ticker_rows = database.fetch_all(
        f"""
        SELECT UPPER(TRIM({quoted_ticker})) AS ticker, MAX(date({quoted_date})) AS latest_date
        FROM "{table_name}"
        WHERE {quoted_ticker} IS NOT NULL AND TRIM({quoted_ticker}) <> '' AND date({quoted_date}) IS NOT NULL{scope_filter_sql}
        GROUP BY UPPER(TRIM({quoted_ticker}))
        """,
        scope_parameters,
    )
    duplicate_row = database.fetch_one(
        f"""
        SELECT COALESCE(SUM(duplicate_count - 1), 0) AS duplicate_rows
        FROM (
            SELECT UPPER(TRIM({quoted_ticker})) AS ticker, date({quoted_date}) AS price_date, COUNT(*) AS duplicate_count
            FROM "{table_name}"
            WHERE {quoted_ticker} IS NOT NULL AND TRIM({quoted_ticker}) <> '' AND date({quoted_date}) IS NOT NULL{scope_filter_sql}
            GROUP BY UPPER(TRIM({quoted_ticker})), date({quoted_date})
            HAVING COUNT(*) > 1
        )
        """,
        scope_parameters,
    )
    invalid_ohlc_row_count = 0
    invalid_ohlc_tickers: set[str] = set()
    if has_all_ohlcv_columns and open_column and high_column and low_column and close_column and volume_column:
        invalid_rows = database.fetch_all(
            f"""
            SELECT UPPER(TRIM({quoted_ticker})) AS ticker
            FROM "{table_name}"
            WHERE {quoted_ticker} IS NOT NULL AND TRIM({quoted_ticker}) <> ''{scope_filter_sql} AND (
                "{open_column}" IS NULL OR
                "{high_column}" IS NULL OR
                "{low_column}" IS NULL OR
                "{close_column}" IS NULL OR
                "{volume_column}" IS NULL OR
                "{low_column}" > "{open_column}" OR
                "{low_column}" > "{high_column}" OR
                "{low_column}" > "{close_column}" OR
                "{open_column}" > "{high_column}" OR
                "{close_column}" > "{high_column}"
            )
            """,
            scope_parameters,
        )
        invalid_ohlc_row_count = len(invalid_rows)
        invalid_ohlc_tickers = {str(row['ticker']) for row in invalid_rows}
    min_max_row = database.fetch_one(
        f"""
        SELECT MIN(date({quoted_date})) AS min_date, MAX(date({quoted_date})) AS max_date
        FROM "{table_name}"
        WHERE {quoted_date} IS NOT NULL{scope_filter_sql}
        """,
        scope_parameters,
    )
    return {
        'rows_by_ticker': {str(row['ticker']): int(row['row_count']) for row in rows_by_ticker_rows},
        'latest_date_by_ticker': {
            str(row['ticker']): _coerce_optional_date(row['latest_date'])
            for row in latest_by_ticker_rows
        },
        'duplicate_ticker_date_rows': int(duplicate_row['duplicate_rows']) if duplicate_row is not None else 0,
        'invalid_ohlc_row_count': invalid_ohlc_row_count,
        'invalid_ohlc_tickers': invalid_ohlc_tickers,
        'min_price_date': _coerce_optional_date(None if min_max_row is None else min_max_row['min_date']),
        'max_price_date': _coerce_optional_date(None if min_max_row is None else min_max_row['max_date']),
    }


def _scope_filter_clause(quoted_ticker: str, relevant_tickers: set[str]) -> tuple[str, tuple[str, ...]]:
    normalized_tickers = tuple(sorted(ticker.upper() for ticker in relevant_tickers if ticker))
    if not normalized_tickers:
        return '', ()
    placeholders = ', '.join('?' for _ in normalized_tickers)
    return f' AND UPPER(TRIM({quoted_ticker})) IN ({placeholders})', normalized_tickers


def _build_risk_audit_rows(*, is_unsafe_path: bool, benchmark_ticker: str | None) -> tuple[SchemaWriteRiskAuditRow, ...]:
    return (
        SchemaWriteRiskAuditRow('explicit_copied_db_path_only', 'blocked' if is_unsafe_path else 'required', 'Må bruke eksplisitt kopi av SQLite-database, ikke legacy produksjonsbane.'),
        SchemaWriteRiskAuditRow('refuse_original_legacy_db_path_by_default', 'blocked' if is_unsafe_path else 'required', 'Original legacy-DB skal stoppes eller flagges som unsafe før enhver fremtidig skriveflyt.'),
        SchemaWriteRiskAuditRow('backup_required_before_write', 'required', 'Backup av kopi må tas før enhver skriveoperasjon.'),
        SchemaWriteRiskAuditRow('schema_validation_required_before_write', 'required', 'Pris-tabell og nødvendige kolonner må valideres før write-path åpnes.'),
        SchemaWriteRiskAuditRow('transaction_required', 'required', 'Fremtidig refresh må bruke eksplisitt transaksjon.'),
        SchemaWriteRiskAuditRow('duplicate_ticker_date_upsert_policy_required', 'required', 'Det trengs en eksplisitt policy for duplicate ticker/date og upsert.'),
        SchemaWriteRiskAuditRow('benchmark_refresh_required', 'required', f'Benchmark {benchmark_ticker or "ikke angitt"} må oppdateres sammen med universet for deterministisk sammenligning.'),
        SchemaWriteRiskAuditRow('universe_refresh_policy_required', 'required', 'Universe refresh policy må være definert for universe_cache og medlemskap.'),
        SchemaWriteRiskAuditRow('dry_run_mode_required', 'required', 'Fremtidig refresh må ha dry-run før write.'),
        SchemaWriteRiskAuditRow('test_db_required', 'required', 'Testdatabase må brukes før write-path godkjennes.'),
        SchemaWriteRiskAuditRow('no_automatic_ui_writes', 'required', 'UI må ikke skrive data automatisk.'),
    )


def _recommendation(
    *,
    is_unsafe_path: bool,
    required_missing: Sequence[str],
    duplicate_rows: int,
    invalid_ohlc_rows: int,
) -> str:
    if is_unsafe_path:
        return DECISION_BLOCKED_UNSAFE
    if required_missing:
        return DECISION_BLOCKED_SCHEMA
    if duplicate_rows > 0 or invalid_ohlc_rows > 0:
        return DECISION_BLOCKED_QUALITY
    return DECISION_READY


def _render_summary_markdown(result: DataRefreshReadinessResult) -> str:
    lines = [
        '# Data Refresh Readiness Summary',
        '',
        '## Database/schema status',
        '',
        f'- DB path: {result.db_path}',
        f'- Read-only open status: {result.read_only_open_status}',
        f'- Unsafe legacy DB path: {result.unsafe_legacy_db_path}',
        f'- Detected price table: {result.detected_price_table}',
        f'- Selected date column: {result.price_date_column or "none"}',
        f'- Detected universe source: {result.detected_universe_source}',
        f'- Universe id: {result.universe_id}',
        f'- Universe ticker count: {result.universe_ticker_count}',
        f'- Benchmark ticker: {result.benchmark_ticker or "not requested"}',
        f'- Required columns present: {", ".join(result.required_columns_present) or "none"}',
        f'- Required columns missing: {", ".join(result.required_columns_missing) or "none"}',
        f'- Duplicate ticker/date rows: {result.duplicate_ticker_date_rows}',
        f'- Invalid OHLC rows: {result.invalid_ohlc_row_count}',
        '',
        '## Data freshness',
        '',
        f'- Universe max price date: {result.universe_max_price_date or "unknown"}',
        f'- Min latest ticker date: {result.min_latest_ticker_date or "unknown"}',
        f'- Max latest ticker date: {result.max_latest_ticker_date or "unknown"}',
        f'- Benchmark latest date: {result.benchmark_latest_date or "unknown"}',
        f'- Benchmark aligned with universe max: {result.benchmark_aligned_with_universe_max}',
        f'- Stale tickers versus universe max: {result.stale_vs_universe_max_count}',
        f'- Stale tickers versus today (informational only): {result.stale_vs_today_count}',
        '',
        '## Recommendation',
        '',
        f'- {result.recommendation}',
        '',
    ]
    return '\n'.join(lines)


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
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

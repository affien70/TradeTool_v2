from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from tradetool.contracts.models import CoverageReport
from tradetool.data import ReadOnlySQLite, SchemaInspection
from tradetool.universe import UniverseTickerSelection, load_universe_tickers

TICKER_COLUMN_CANDIDATES = ('ticker', 'symbol', 'ric')
DATE_COLUMN_CANDIDATES = ('date', 'trade_date', 'price_date', 'as_of_date')
PRICE_VALUE_COLUMN_CANDIDATES = ('close', 'adj_close', 'adjusted_close', 'last')


@dataclass(frozen=True, slots=True)
class CoverageDiagnosticsResult:
    report: CoverageReport
    schema: SchemaInspection
    db_path: Path
    universe_id: str
    universe_source: str
    price_table: str
    ticker_column: str
    date_column: str
    row_count: int
    min_price_date: str | None
    max_price_date: str | None
    latest_data_date_distribution_rows: tuple[tuple[str, int], ...]
    tickers_with_at_least_252_rows: int
    tickers_with_at_least_504_rows: int
    fresh_latest_data_count: int
    duplicate_ticker_date_rows: int
    missing_or_invalid_row_count: int
    invalid_date_value_count: int
    missing_price_samples: tuple[Mapping[str, str | None], ...]
    missing_universe_tickers: tuple[str, ...]
    stale_universe_tickers: tuple[str, ...]
    placeholder_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            'db_path': str(self.db_path),
            'universe_id': self.universe_id,
            'universe_source': self.universe_source,
            'price_table': self.price_table,
            'ticker_column': self.ticker_column,
            'date_column': self.date_column,
            'row_count': self.row_count,
            'min_price_date': self.min_price_date,
            'max_price_date': self.max_price_date,
            'latest_data_date_distribution_rows': [
                {'latest_data_date': key, 'ticker_count': value}
                for key, value in self.latest_data_date_distribution_rows
            ],
            'tickers_with_at_least_252_rows': self.tickers_with_at_least_252_rows,
            'tickers_with_at_least_504_rows': self.tickers_with_at_least_504_rows,
            'fresh_latest_data_count': self.fresh_latest_data_count,
            'duplicate_ticker_date_rows': self.duplicate_ticker_date_rows,
            'missing_or_invalid_row_count': self.missing_or_invalid_row_count,
            'invalid_date_value_count': self.invalid_date_value_count,
            'missing_price_samples': [dict(sample) for sample in self.missing_price_samples],
            'missing_universe_tickers': list(self.missing_universe_tickers),
            'stale_universe_tickers': list(self.stale_universe_tickers),
            'placeholder_counts': dict(self.placeholder_counts),
            'schema': {
                'tables': list(self.schema.tables),
                'price_history_candidates': list(self.schema.price_history_candidates),
                'universe_candidates': list(self.schema.universe_candidates),
                'warnings': list(self.schema.warnings),
                'columns_by_table': {
                    table: [
                        {
                            'name': column.name,
                            'declared_type': column.declared_type,
                            'not_null': column.not_null,
                            'default_value': column.default_value,
                            'primary_key_position': column.primary_key_position,
                        }
                        for column in columns
                    ]
                    for table, columns in self.schema.columns_by_table.items()
                },
            },
            'coverage_report': {
                'run_id': self.report.run_id,
                'run_date': self.report.run_date.isoformat(),
                'input_universe_count': self.report.input_universe_count,
                'valid_ticker_count': self.report.valid_ticker_count,
                'market_data_coverage_count': self.report.market_data_coverage_count,
                'enough_history_count': self.report.enough_history_count,
                'feature_complete_count': self.report.feature_complete_count,
                'eligible_count': self.report.eligible_count,
                'ranked_count': self.report.ranked_count,
                'failed_count': self.report.failed_count,
                'rejection_counts_by_reason': dict(self.report.rejection_counts_by_reason),
                'latest_data_date_distribution': dict(self.report.latest_data_date_distribution),
                'benchmark_coverage_notes': list(self.report.benchmark_coverage_notes),
                'missing_ticker_samples': list(self.report.missing_ticker_samples),
            },
        }


def build_coverage_diagnostics(
    *,
    db_path: str | Path,
    universe_id: str,
    explicit_tickers: Sequence[str] | None = None,
    universe_csv_path: str | Path | None = None,
    price_table: str | None = None,
) -> CoverageDiagnosticsResult:
    database = ReadOnlySQLite(db_path)
    schema = database.inspect_schema()
    resolved_price_table = price_table or _resolve_price_table(schema)
    ticker_column = _resolve_column_name(schema, resolved_price_table, TICKER_COLUMN_CANDIDATES, 'ticker')
    date_column = _resolve_column_name(schema, resolved_price_table, DATE_COLUMN_CANDIDATES, 'date')
    price_value_column = _resolve_column_name(schema, resolved_price_table, PRICE_VALUE_COLUMN_CANDIDATES, 'close')
    universe = load_universe_tickers(
        universe_id=universe_id,
        explicit_tickers=explicit_tickers,
        csv_path=universe_csv_path,
        database=None if (explicit_tickers is not None or universe_csv_path is not None) else database,
        price_table=resolved_price_table,
        ticker_column=ticker_column,
    )
    stats = _collect_price_history_statistics(
        database=database,
        table_name=resolved_price_table,
        ticker_column=ticker_column,
        date_column=date_column,
        price_value_column=price_value_column,
    )
    latest_distribution = dict(stats['latest_data_date_distribution'])
    enough_history_count = sum(1 for _, value in stats['rows_by_ticker'].items() if value >= 252)
    enough_history_504_count = sum(1 for _, value in stats['rows_by_ticker'].items() if value >= 504)
    universe_tickers = set(universe.tickers)
    present_tickers = universe_tickers.intersection(stats['rows_by_ticker'].keys())
    missing_tickers = tuple(sorted(universe_tickers.difference(present_tickers)))
    max_price_date = stats['max_price_date']
    stale_tickers = tuple(
        sorted(
            ticker
            for ticker in present_tickers
            if (
                ticker not in stats['latest_date_by_ticker']
                or (max_price_date is not None and stats['latest_date_by_ticker'][ticker] != max_price_date)
            )
        )
    )
    market_data_coverage_count = len(present_tickers)
    enough_history_present_count = sum(1 for ticker in present_tickers if stats['rows_by_ticker'][ticker] >= 252)
    feature_complete_count = enough_history_present_count
    eligible_count = enough_history_present_count
    ranked_count = 0
    placeholder_counts = {
        'feature_complete_placeholder_count': feature_complete_count,
        'eligible_placeholder_count': eligible_count,
        'ranked_placeholder_count': ranked_count,
    }
    rejection_counts = {
        'invalid_ticker': len(universe.invalid_values),
        'missing_market_data': len(missing_tickers),
        'insufficient_history_lt_252': market_data_coverage_count - enough_history_present_count,
        'stale_latest_data': len(stale_tickers),
        'duplicate_ticker_date_rows': stats['duplicate_ticker_date_rows'],
    }
    failed_count = len(universe.invalid_values) + len(missing_tickers)
    run_timestamp = datetime.now(UTC)
    report = CoverageReport(
        run_id=f'coverage-{run_timestamp.strftime("%Y%m%dT%H%M%SZ")}',
        run_date=run_timestamp.date(),
        input_universe_count=universe.input_count,
        valid_ticker_count=len(universe.tickers),
        market_data_coverage_count=market_data_coverage_count,
        enough_history_count=enough_history_present_count,
        feature_complete_count=feature_complete_count,
        eligible_count=eligible_count,
        ranked_count=ranked_count,
        failed_count=failed_count,
        rejection_counts_by_reason=rejection_counts,
        latest_data_date_distribution=latest_distribution,
        benchmark_coverage_notes=(
            'Feature-complete and eligible counts are Phase 3a placeholders derived from price-history coverage only.',
            'Ranked count remains zero until ranking logic is implemented in a later phase.',
        ),
        missing_ticker_samples=missing_tickers[:10],
    )
    return CoverageDiagnosticsResult(
        report=report,
        schema=schema,
        db_path=Path(db_path).expanduser().resolve(),
        universe_id=universe_id,
        universe_source=universe.source,
        price_table=resolved_price_table,
        ticker_column=ticker_column,
        date_column=date_column,
        row_count=stats['row_count'],
        min_price_date=None if stats['min_price_date'] is None else stats['min_price_date'].isoformat(),
        max_price_date=None if stats['max_price_date'] is None else stats['max_price_date'].isoformat(),
        latest_data_date_distribution_rows=tuple(sorted(latest_distribution.items())),
        tickers_with_at_least_252_rows=sum(1 for value in stats['rows_by_ticker'].values() if value >= 252),
        tickers_with_at_least_504_rows=enough_history_504_count,
        fresh_latest_data_count=sum(1 for value in stats['latest_date_by_ticker'].values() if value == max_price_date),
        duplicate_ticker_date_rows=stats['duplicate_ticker_date_rows'],
        missing_or_invalid_row_count=stats['missing_or_invalid_row_count'],
        invalid_date_value_count=stats['invalid_date_value_count'],
        missing_price_samples=tuple(stats['missing_price_samples']),
        missing_universe_tickers=missing_tickers,
        stale_universe_tickers=stale_tickers,
        placeholder_counts=placeholder_counts,
    )


def _resolve_price_table(schema: SchemaInspection) -> str:
    if not schema.price_history_candidates:
        raise ValueError('Unable to detect a price-history table in the SQLite schema.')
    return schema.price_history_candidates[0]


def _resolve_column_name(schema: SchemaInspection, table_name: str, candidates: Sequence[str], label: str) -> str:
    columns = schema.columns_by_table.get(table_name)
    if columns is None:
        raise ValueError(f'Table not found in schema inspection: {table_name}')
    normalized = {column.name.lower(): column.name for column in columns}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    raise ValueError(f'Unable to detect a {label} column for table "{table_name}".')


def _collect_price_history_statistics(
    *,
    database: ReadOnlySQLite,
    table_name: str,
    ticker_column: str,
    date_column: str,
    price_value_column: str,
) -> dict[str, object]:
    quoted_ticker = f'"{ticker_column}"'
    quoted_date = f'"{date_column}"'
    quoted_price = f'"{price_value_column}"'
    row_count_row = database.fetch_one(f'SELECT COUNT(*) AS count FROM "{table_name}"')
    min_max_row = database.fetch_one(
        f"""
        SELECT MIN(date({quoted_date})) AS min_date, MAX(date({quoted_date})) AS max_date
        FROM "{table_name}"
        WHERE {quoted_date} IS NOT NULL
        """
    )
    rows_by_ticker_rows = database.fetch_all(
        f"""
        SELECT UPPER(TRIM({quoted_ticker})) AS ticker, COUNT(*) AS row_count
        FROM "{table_name}"
        WHERE {quoted_ticker} IS NOT NULL AND TRIM({quoted_ticker}) <> ''
        GROUP BY UPPER(TRIM({quoted_ticker}))
        """
    )
    latest_by_ticker_rows = database.fetch_all(
        f"""
        SELECT ticker, latest_date
        FROM (
            SELECT
                UPPER(TRIM({quoted_ticker})) AS ticker,
                MAX(date({quoted_date})) AS latest_date
            FROM "{table_name}"
            WHERE {quoted_ticker} IS NOT NULL AND TRIM({quoted_ticker}) <> '' AND date({quoted_date}) IS NOT NULL
            GROUP BY UPPER(TRIM({quoted_ticker}))
        )
        ORDER BY ticker
        """
    )
    latest_distribution_rows = database.fetch_all(
        f"""
        WITH latest_dates AS (
            SELECT
                UPPER(TRIM({quoted_ticker})) AS ticker,
                MAX(date({quoted_date})) AS latest_date
            FROM "{table_name}"
            WHERE {quoted_ticker} IS NOT NULL AND TRIM({quoted_ticker}) <> '' AND date({quoted_date}) IS NOT NULL
            GROUP BY UPPER(TRIM({quoted_ticker}))
        )
        SELECT latest_date, COUNT(*) AS ticker_count
        FROM latest_dates
        GROUP BY latest_date
        ORDER BY latest_date
        """
    )
    duplicate_row = database.fetch_one(
        f"""
        SELECT COALESCE(SUM(duplicate_count - 1), 0) AS duplicate_rows
        FROM (
            SELECT UPPER(TRIM({quoted_ticker})) AS ticker, date({quoted_date}) AS price_date, COUNT(*) AS duplicate_count
            FROM "{table_name}"
            WHERE {quoted_ticker} IS NOT NULL AND TRIM({quoted_ticker}) <> '' AND date({quoted_date}) IS NOT NULL
            GROUP BY UPPER(TRIM({quoted_ticker})), date({quoted_date})
            HAVING COUNT(*) > 1
        )
        """
    )
    missing_invalid_row = database.fetch_one(
        f"""
        SELECT COUNT(*) AS invalid_count
        FROM "{table_name}"
        WHERE
            {quoted_ticker} IS NULL OR TRIM({quoted_ticker}) = '' OR
            {quoted_date} IS NULL OR TRIM({quoted_date}) = '' OR
            {quoted_price} IS NULL
        """
    )
    invalid_date_row = database.fetch_one(
        f"""
        SELECT COUNT(*) AS invalid_date_count
        FROM "{table_name}"
        WHERE {quoted_date} IS NOT NULL AND TRIM({quoted_date}) <> '' AND date({quoted_date}) IS NULL
        """
    )
    sample_rows = database.fetch_all(
        f"""
        SELECT
            {quoted_ticker} AS ticker,
            {quoted_date} AS price_date,
            {quoted_price} AS price_value
        FROM "{table_name}"
        WHERE
            {quoted_ticker} IS NULL OR TRIM({quoted_ticker}) = '' OR
            {quoted_date} IS NULL OR TRIM({quoted_date}) = '' OR
            {quoted_price} IS NULL OR
            ({quoted_date} IS NOT NULL AND TRIM({quoted_date}) <> '' AND date({quoted_date}) IS NULL)
        LIMIT 5
        """
    )
    rows_by_ticker = {str(row['ticker']): int(row['row_count']) for row in rows_by_ticker_rows}
    latest_date_by_ticker = {
        str(row['ticker']): date.fromisoformat(str(row['latest_date']))
        for row in latest_by_ticker_rows
        if row['latest_date'] is not None
    }
    latest_distribution = Counter(
        {
            str(row['latest_date']): int(row['ticker_count'])
            for row in latest_distribution_rows
            if row['latest_date'] is not None
        }
    )
    return {
        'row_count': int(row_count_row['count']) if row_count_row is not None else 0,
        'min_price_date': _coerce_optional_date(min_max_row['min_date'] if min_max_row is not None else None),
        'max_price_date': _coerce_optional_date(min_max_row['max_date'] if min_max_row is not None else None),
        'rows_by_ticker': rows_by_ticker,
        'latest_date_by_ticker': latest_date_by_ticker,
        'latest_data_date_distribution': latest_distribution,
        'duplicate_ticker_date_rows': int(duplicate_row['duplicate_rows']) if duplicate_row is not None else 0,
        'missing_or_invalid_row_count': int(missing_invalid_row['invalid_count']) if missing_invalid_row is not None else 0,
        'invalid_date_value_count': int(invalid_date_row['invalid_date_count']) if invalid_date_row is not None else 0,
        'missing_price_samples': tuple(
            {
                'ticker': None if row['ticker'] is None else str(row['ticker']),
                'price_date': None if row['price_date'] is None else str(row['price_date']),
                'price_value': None if row['price_value'] is None else str(row['price_value']),
            }
            for row in sample_rows
        ),
    }


def _coerce_optional_date(value: object) -> date | None:
    if value in (None, ''):
        return None
    return date.fromisoformat(str(value))

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from tradetool.contracts.models import EligibilityResult
from tradetool.data import ReadOnlySQLite, SchemaInspection
from tradetool.diagnostics.coverage import (
    DATE_COLUMN_CANDIDATES,
    PRICE_VALUE_COLUMN_CANDIDATES,
    TICKER_COLUMN_CANDIDATES,
    _coerce_optional_date,
    _resolve_column_name,
    _resolve_price_table,
)
from tradetool.policy import (
    StructuralEligibilityInput,
    build_eligibility_results,
    summarize_eligibility_results,
)
from tradetool.universe import load_universe_tickers

REQUIRED_OHLCV_COLUMNS = ('open', 'high', 'low', 'close', 'volume')


@dataclass(frozen=True, slots=True)
class EligibilityDiagnosticRow:
    ticker: str
    eligible: bool
    rejection_reasons: tuple[str, ...]
    row_count: int
    latest_price_date: str | None
    universe_max_price_date: str | None
    has_market_data: bool
    has_duplicate_rows: bool
    has_invalid_ohlc: bool
    source_universe: str

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'eligible': self.eligible,
            'rejection_reasons': list(self.rejection_reasons),
            'row_count': self.row_count,
            'latest_price_date': self.latest_price_date,
            'universe_max_price_date': self.universe_max_price_date,
            'has_market_data': self.has_market_data,
            'has_duplicate_rows': self.has_duplicate_rows,
            'has_invalid_ohlc': self.has_invalid_ohlc,
            'source_universe': self.source_universe,
        }


@dataclass(frozen=True, slots=True)
class EligibilityDiagnosticsResult:
    db_path: Path
    universe_id: str
    universe_source: str
    price_table: str
    ticker_column: str
    date_column: str
    max_price_date: str | None
    min_history_rows: int
    generated_at_utc: str
    results: tuple[EligibilityDiagnosticRow, ...]
    eligibility_results: tuple[EligibilityResult, ...]
    input_universe_count: int
    eligible_count: int
    rejected_count: int
    rejection_counts_by_reason: Mapping[str, int]
    schema: SchemaInspection

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'universe_id': self.universe_id,
            'universe_source': self.universe_source,
            'input_universe_count': self.input_universe_count,
            'eligible_count': self.eligible_count,
            'rejected_count': self.rejected_count,
            'rejection_counts_by_reason': dict(self.rejection_counts_by_reason),
            'min_history_rows': self.min_history_rows,
            'max_price_date': self.max_price_date,
            'generated_at_utc': self.generated_at_utc,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.to_summary_dict(),
            'db_path': str(self.db_path),
            'price_table': self.price_table,
            'ticker_column': self.ticker_column,
            'date_column': self.date_column,
            'results': [row.to_dict() for row in self.results],
            'schema': {
                'tables': list(self.schema.tables),
                'price_history_candidates': list(self.schema.price_history_candidates),
                'universe_candidates': list(self.schema.universe_candidates),
                'warnings': list(self.schema.warnings),
            },
        }


def build_eligibility_diagnostics(
    *,
    db_path: str | Path,
    universe_id: str,
    explicit_tickers: Sequence[str] | None = None,
    universe_csv_path: str | Path | None = None,
    price_table: str | None = None,
    min_history_rows: int = 252,
    freshness_tolerance_days: int = 0,
) -> EligibilityDiagnosticsResult:
    database = ReadOnlySQLite(db_path)
    schema = database.inspect_schema()
    resolved_price_table = price_table or _resolve_price_table(schema)
    ticker_column = _resolve_column_name(schema, resolved_price_table, TICKER_COLUMN_CANDIDATES, 'ticker')
    date_column = _resolve_column_name(schema, resolved_price_table, DATE_COLUMN_CANDIDATES, 'date')
    universe = load_universe_tickers(
        universe_id=universe_id,
        explicit_tickers=explicit_tickers,
        csv_path=universe_csv_path,
        database=None if (explicit_tickers is not None or universe_csv_path is not None) else database,
        price_table=resolved_price_table,
        ticker_column=ticker_column,
    )
    table_columns = {column.name.lower(): column.name for column in schema.columns_by_table.get(resolved_price_table, ())}
    has_required_ohlcv_columns = all(column_name in table_columns for column_name in REQUIRED_OHLCV_COLUMNS)
    per_ticker_stats = _collect_per_ticker_eligibility_stats(
        database=database,
        table_name=resolved_price_table,
        ticker_column=ticker_column,
        date_column=date_column,
        table_columns=table_columns,
    )

    generated_at = datetime.now(UTC)
    feature_date = per_ticker_stats['max_price_date'] or generated_at.date()
    inputs: list[StructuralEligibilityInput] = []
    display_tickers: list[str] = []
    for ticker in universe.tickers:
        inputs.append(
            StructuralEligibilityInput(
                ticker=ticker,
                feature_date=feature_date,
                row_count=per_ticker_stats['rows_by_ticker'].get(ticker, 0),
                latest_price_date=per_ticker_stats['latest_date_by_ticker'].get(ticker),
                universe_max_price_date=per_ticker_stats['max_price_date'],
                has_market_data=ticker in per_ticker_stats['rows_by_ticker'],
                has_duplicate_rows=ticker in per_ticker_stats['tickers_with_duplicates'],
                has_invalid_ohlc=ticker in per_ticker_stats['tickers_with_invalid_ohlc'],
                has_required_ohlcv_columns=has_required_ohlcv_columns,
                is_ticker_valid=True,
                source_universe=universe.source,
            )
        )
        display_tickers.append(ticker)
    for index, invalid_value in enumerate(universe.invalid_values, start=1):
        display_ticker = f'<INVALID_TICKER_{index}>'
        inputs.append(
            StructuralEligibilityInput(
                ticker=display_ticker,
                feature_date=feature_date,
                row_count=0,
                latest_price_date=None,
                universe_max_price_date=per_ticker_stats['max_price_date'],
                has_market_data=False,
                has_duplicate_rows=False,
                has_invalid_ohlc=False,
                has_required_ohlcv_columns=has_required_ohlcv_columns,
                is_ticker_valid=False,
                source_universe=universe.source,
            )
        )
        display_tickers.append(display_ticker if invalid_value == '' else f'{display_ticker}:{invalid_value}')
    eligibility_results = build_eligibility_results(
        inputs,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    summary = summarize_eligibility_results(eligibility_results)

    diagnostic_rows = tuple(
        EligibilityDiagnosticRow(
            ticker=display_ticker,
            eligible=result.eligible,
            rejection_reasons=result.rejection_reasons,
            row_count=eligibility_input.row_count,
            latest_price_date=None if eligibility_input.latest_price_date is None else eligibility_input.latest_price_date.isoformat(),
            universe_max_price_date=None if eligibility_input.universe_max_price_date is None else eligibility_input.universe_max_price_date.isoformat(),
            has_market_data=eligibility_input.has_market_data,
            has_duplicate_rows=eligibility_input.has_duplicate_rows,
            has_invalid_ohlc=eligibility_input.has_invalid_ohlc,
            source_universe=universe.source,
        )
        for display_ticker, eligibility_input, result in zip(display_tickers, inputs, eligibility_results, strict=True)
    )

    return EligibilityDiagnosticsResult(
        db_path=Path(db_path).expanduser().resolve(),
        universe_id=universe_id,
        universe_source=universe.source,
        price_table=resolved_price_table,
        ticker_column=ticker_column,
        date_column=date_column,
        max_price_date=None if per_ticker_stats['max_price_date'] is None else per_ticker_stats['max_price_date'].isoformat(),
        min_history_rows=min_history_rows,
        generated_at_utc=generated_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
        results=diagnostic_rows,
        eligibility_results=eligibility_results,
        input_universe_count=universe.input_count,
        eligible_count=int(summary['eligible_count']),
        rejected_count=int(summary['rejected_count']),
        rejection_counts_by_reason=summary['rejection_counts_by_reason'],
        schema=schema,
    )


def _collect_per_ticker_eligibility_stats(
    *,
    database: ReadOnlySQLite,
    table_name: str,
    ticker_column: str,
    date_column: str,
    table_columns: Mapping[str, str],
) -> dict[str, object]:
    quoted_ticker = f'"{ticker_column}"'
    quoted_date = f'"{date_column}"'
    row_count_rows = database.fetch_all(
        f"""
        SELECT UPPER(TRIM({quoted_ticker})) AS ticker, COUNT(*) AS row_count
        FROM "{table_name}"
        WHERE {quoted_ticker} IS NOT NULL AND TRIM({quoted_ticker}) <> ''
        GROUP BY UPPER(TRIM({quoted_ticker}))
        """
    )
    latest_date_rows = database.fetch_all(
        f"""
        SELECT UPPER(TRIM({quoted_ticker})) AS ticker, MAX(date({quoted_date})) AS latest_date
        FROM "{table_name}"
        WHERE {quoted_ticker} IS NOT NULL AND TRIM({quoted_ticker}) <> '' AND date({quoted_date}) IS NOT NULL
        GROUP BY UPPER(TRIM({quoted_ticker}))
        """
    )
    duplicate_rows = database.fetch_all(
        f"""
        SELECT ticker
        FROM (
            SELECT UPPER(TRIM({quoted_ticker})) AS ticker, date({quoted_date}) AS price_date, COUNT(*) AS duplicate_count
            FROM "{table_name}"
            WHERE {quoted_ticker} IS NOT NULL AND TRIM({quoted_ticker}) <> '' AND date({quoted_date}) IS NOT NULL
            GROUP BY UPPER(TRIM({quoted_ticker})), date({quoted_date})
            HAVING COUNT(*) > 1
        )
        """
    )
    min_max_row = database.fetch_one(
        f"""
        SELECT MIN(date({quoted_date})) AS min_date, MAX(date({quoted_date})) AS max_date
        FROM "{table_name}"
        WHERE {quoted_date} IS NOT NULL
        """
    )

    invalid_ohlc_tickers: set[str] = set()
    if all(column_name in table_columns for column_name in REQUIRED_OHLCV_COLUMNS):
        open_column = f'"{table_columns["open"]}"'
        high_column = f'"{table_columns["high"]}"'
        low_column = f'"{table_columns["low"]}"'
        close_column = f'"{table_columns["close"]}"'
        volume_column = f'"{table_columns["volume"]}"'
        invalid_ohlc_rows = database.fetch_all(
            f"""
            SELECT DISTINCT UPPER(TRIM({quoted_ticker})) AS ticker
            FROM "{table_name}"
            WHERE {quoted_ticker} IS NOT NULL AND TRIM({quoted_ticker}) <> '' AND (
                {open_column} IS NULL OR
                {high_column} IS NULL OR
                {low_column} IS NULL OR
                {close_column} IS NULL OR
                {volume_column} IS NULL OR
                {low_column} > {high_column}
            )
            """
        )
        invalid_ohlc_tickers = {str(row['ticker']) for row in invalid_ohlc_rows}

    return {
        'rows_by_ticker': {str(row['ticker']): int(row['row_count']) for row in row_count_rows},
        'latest_date_by_ticker': {
            str(row['ticker']): date.fromisoformat(str(row['latest_date']))
            for row in latest_date_rows
            if row['latest_date'] is not None
        },
        'tickers_with_duplicates': {str(row['ticker']) for row in duplicate_rows},
        'tickers_with_invalid_ohlc': invalid_ohlc_tickers,
        'min_price_date': _coerce_optional_date(min_max_row['min_date'] if min_max_row is not None else None),
        'max_price_date': _coerce_optional_date(min_max_row['max_date'] if min_max_row is not None else None),
    }

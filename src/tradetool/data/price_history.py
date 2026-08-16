from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from tradetool.data.sqlite_readonly import ReadOnlySQLite, SchemaInspection

TICKER_COLUMN_CANDIDATES = ('ticker', 'symbol', 'ric')
DATE_COLUMN_CANDIDATES = ('date', 'price_date', 'trade_date', 'as_of_date')
OHLCV_COLUMN_CANDIDATES: Mapping[str, tuple[str, ...]] = {
    'open': ('open',),
    'high': ('high',),
    'low': ('low',),
    'close': ('close', 'adj_close', 'adjusted_close', 'last'),
    'volume': ('volume',),
}


@dataclass(frozen=True, slots=True)
class PriceHistoryRecord:
    ticker: str
    price_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class PriceHistoryLoadResult:
    table_name: str
    ticker_column: str
    date_column: str
    columns: Mapping[str, str]
    rows_by_ticker: Mapping[str, tuple[PriceHistoryRecord, ...]]


def load_price_history_for_tickers(
    *,
    database: ReadOnlySQLite,
    tickers: Sequence[str],
    price_table: str = 'price_history',
    schema: SchemaInspection | None = None,
) -> PriceHistoryLoadResult:
    resolved_schema = schema or database.inspect_schema()
    columns = _resolve_price_table_columns(resolved_schema=resolved_schema, table_name=price_table)
    normalized_tickers = tuple(sorted({str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()}))
    if not normalized_tickers:
        return PriceHistoryLoadResult(
            table_name=price_table,
            ticker_column=columns['ticker'],
            date_column=columns['date'],
            columns=columns,
            rows_by_ticker={},
        )
    placeholders = ', '.join('?' for _ in normalized_tickers)
    quoted_ticker = f'"{columns["ticker"]}"'
    quoted_date = f'"{columns["date"]}"'
    query = f"""
        SELECT
            UPPER(TRIM({quoted_ticker})) AS ticker,
            date({quoted_date}) AS price_date,
            "{columns["open"]}" AS open_value,
            "{columns["high"]}" AS high_value,
            "{columns["low"]}" AS low_value,
            "{columns["close"]}" AS close_value,
            "{columns["volume"]}" AS volume_value
        FROM "{price_table}"
        WHERE UPPER(TRIM({quoted_ticker})) IN ({placeholders}) AND date({quoted_date}) IS NOT NULL
        ORDER BY UPPER(TRIM({quoted_ticker})), date({quoted_date})
    """
    rows = database.fetch_all(query, normalized_tickers)
    rows_by_ticker: dict[str, list[PriceHistoryRecord]] = defaultdict(list)
    for row in rows:
        rows_by_ticker[str(row['ticker'])].append(
            PriceHistoryRecord(
                ticker=str(row['ticker']),
                price_date=date.fromisoformat(str(row['price_date'])),
                open=float(row['open_value']) if row['open_value'] is not None else float('nan'),
                high=float(row['high_value']) if row['high_value'] is not None else float('nan'),
                low=float(row['low_value']) if row['low_value'] is not None else float('nan'),
                close=float(row['close_value']) if row['close_value'] is not None else float('nan'),
                volume=float(row['volume_value']) if row['volume_value'] is not None else float('nan'),
            )
        )
    return PriceHistoryLoadResult(
        table_name=price_table,
        ticker_column=columns['ticker'],
        date_column=columns['date'],
        columns=columns,
        rows_by_ticker={ticker: tuple(records) for ticker, records in rows_by_ticker.items()},
    )


def _resolve_price_table_columns(*, resolved_schema: SchemaInspection, table_name: str) -> dict[str, str]:
    columns = resolved_schema.columns_by_table.get(table_name)
    if columns is None:
        raise ValueError(f'Price table not found in schema: {table_name}')
    normalized = {column.name.lower(): column.name for column in columns}
    resolved: dict[str, str] = {
        'ticker': _resolve_column(normalized, TICKER_COLUMN_CANDIDATES, 'ticker'),
        'date': _resolve_column(normalized, DATE_COLUMN_CANDIDATES, 'date'),
    }
    for field_name, candidates in OHLCV_COLUMN_CANDIDATES.items():
        resolved[field_name] = _resolve_column(normalized, candidates, field_name)
    return resolved


def _resolve_column(normalized: Mapping[str, str], candidates: Sequence[str], label: str) -> str:
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    raise ValueError(f'Missing required {label} column in price-history table.')

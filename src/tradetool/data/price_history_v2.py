from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from tradetool.data.market_data_schema import V2_PRICE_TABLE_NAME, validate_market_data_db_path
from tradetool.data.price_history import PriceHistoryRecord
from tradetool.data.sqlite_readonly import ReadOnlySQLite, SchemaInspection


@dataclass(frozen=True, slots=True)
class PriceHistoryV2Record:
    ticker: str
    price_date: date
    open: float
    high: float
    low: float
    close: float
    raw_close: float
    adjusted_close: float
    volume: float
    data_source: str

    def to_feature_record(self) -> PriceHistoryRecord:
        # Phase 4i keeps raw_close available for diagnostics while routing
        # feature calculations through adjusted_close as the effective close.
        return PriceHistoryRecord(
            ticker=self.ticker,
            price_date=self.price_date,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )


@dataclass(frozen=True, slots=True)
class PriceHistoryV2LoadResult:
    table_name: str
    data_source: str
    rows_by_ticker: Mapping[str, tuple[PriceHistoryV2Record, ...]]

    def as_feature_rows_by_ticker(self) -> Mapping[str, tuple[PriceHistoryRecord, ...]]:
        return {
            ticker: tuple(row.to_feature_record() for row in rows)
            for ticker, rows in self.rows_by_ticker.items()
        }


def load_price_history_v2_for_tickers(
    *,
    db_path: str,
    tickers: Sequence[str],
    data_source: str = 'yahoo',
    max_price_date: date | None = None,
    database: ReadOnlySQLite | None = None,
    schema: SchemaInspection | None = None,
) -> PriceHistoryV2LoadResult:
    resolved = validate_market_data_db_path(db_path)
    if not resolved.exists():
        raise FileNotFoundError(f'V2 market-data DB path does not exist: {resolved}')
    readonly_db = database or ReadOnlySQLite(resolved)
    resolved_schema = schema or readonly_db.inspect_schema()
    if V2_PRICE_TABLE_NAME not in resolved_schema.columns_by_table:
        raise ValueError(f'V2 market-data table not found in schema: {V2_PRICE_TABLE_NAME}')

    normalized_tickers = tuple(sorted({str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()}))
    normalized_source = data_source.strip()
    if not normalized_tickers:
        return PriceHistoryV2LoadResult(
            table_name=V2_PRICE_TABLE_NAME,
            data_source=normalized_source,
            rows_by_ticker={},
        )

    placeholders = ', '.join('?' for _ in normalized_tickers)
    query = f"""
        SELECT
            UPPER(TRIM(ticker)) AS ticker,
            date(price_date) AS price_date,
            raw_open,
            raw_high,
            raw_low,
            raw_close,
            adjusted_close,
            volume,
            data_source
        FROM "{V2_PRICE_TABLE_NAME}"
        WHERE UPPER(TRIM(ticker)) IN ({placeholders})
          AND data_source = ?
          AND date(price_date) IS NOT NULL
          AND (? IS NULL OR date(price_date) <= date(?))
        ORDER BY UPPER(TRIM(ticker)), date(price_date)
    """
    max_date_text = None if max_price_date is None else max_price_date.isoformat()
    rows = readonly_db.fetch_all(query, (*normalized_tickers, normalized_source, max_date_text, max_date_text))
    rows_by_ticker: dict[str, list[PriceHistoryV2Record]] = defaultdict(list)
    for row in rows:
        adjusted_close = float(row['adjusted_close'])
        rows_by_ticker[str(row['ticker'])].append(
            PriceHistoryV2Record(
                ticker=str(row['ticker']),
                price_date=date.fromisoformat(str(row['price_date'])),
                open=float(row['raw_open']) if row['raw_open'] is not None else float('nan'),
                high=float(row['raw_high']) if row['raw_high'] is not None else float('nan'),
                low=float(row['raw_low']) if row['raw_low'] is not None else float('nan'),
                close=adjusted_close,
                raw_close=float(row['raw_close']) if row['raw_close'] is not None else float('nan'),
                adjusted_close=adjusted_close,
                volume=float(row['volume']) if row['volume'] is not None else float('nan'),
                data_source=str(row['data_source']),
            )
        )
    return PriceHistoryV2LoadResult(
        table_name=V2_PRICE_TABLE_NAME,
        data_source=normalized_source,
        rows_by_ticker={ticker: tuple(records) for ticker, records in rows_by_ticker.items()},
    )

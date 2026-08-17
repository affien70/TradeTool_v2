from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SourceMarketDataRow:
    ticker: str
    price_date: str
    raw_open: float | None
    raw_high: float | None
    raw_low: float | None
    raw_close: float | None
    adjusted_close: float | None
    volume: float | None
    data_source: str
    warning_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TickerFetchStatus:
    ticker: str
    fetched: bool
    row_count: int
    status: str
    warning_codes: tuple[str, ...] = ()
    message: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'fetched': self.fetched,
            'row_count': self.row_count,
            'status': self.status,
            'warning_codes': '; '.join(self.warning_codes),
            'message': self.message,
        }


@dataclass(frozen=True, slots=True)
class MarketDataSourceFetchResult:
    source_name: str
    requested_tickers: tuple[str, ...]
    rows: tuple[SourceMarketDataRow, ...]
    ticker_statuses: tuple[TickerFetchStatus, ...]
    provider_warning: str | None = None
    provider_error: str | None = None


class MarketDataSourceError(RuntimeError):
    pass


class MarketDataSourceDependencyError(MarketDataSourceError):
    pass


class MarketDataSource(Protocol):
    source_name: str

    def fetch_daily_rows(
        self,
        *,
        tickers: Sequence[str],
        start_date: date,
        end_date: date,
    ) -> MarketDataSourceFetchResult: ...


class YahooMarketDataSource:
    source_name = 'yahoo'

    def fetch_daily_rows(
        self,
        *,
        tickers: Sequence[str],
        start_date: date,
        end_date: date,
    ) -> MarketDataSourceFetchResult:
        try:
            import yfinance as yf  # type: ignore
        except ModuleNotFoundError as exc:
            raise MarketDataSourceDependencyError(
                'yfinance is not installed in this environment; yahoo dry-run fetch cannot execute.'
            ) from exc

        normalized_tickers = tuple(_normalize_requested_ticker(ticker) for ticker in tickers if ticker.strip())
        rows: list[SourceMarketDataRow] = []
        statuses: list[TickerFetchStatus] = []
        start_value = start_date.isoformat()
        end_exclusive = end_date.fromordinal(end_date.toordinal() + 1).isoformat()
        for ticker in normalized_tickers:
            history = yf.download(
                tickers=ticker,
                start=start_value,
                end=end_exclusive,
                auto_adjust=False,
                progress=False,
                actions=False,
                group_by='column',
            )
            if history is None or getattr(history, 'empty', True):
                statuses.append(
                    TickerFetchStatus(
                        ticker=ticker,
                        fetched=False,
                        row_count=0,
                        status='missing',
                        message='No rows returned from yahoo for the requested date range.',
                    )
                )
                continue

            row_count = 0
            warning_codes: set[str] = set()
            for index_value, row in history.iterrows():
                normalized = _normalize_yahoo_row(ticker=ticker, index_value=index_value, row_mapping=row.to_dict())
                rows.append(normalized)
                warning_codes.update(normalized.warning_codes)
                row_count += 1
            statuses.append(
                TickerFetchStatus(
                    ticker=ticker,
                    fetched=True,
                    row_count=row_count,
                    status='fetched',
                    warning_codes=tuple(sorted(warning_codes)),
                )
            )
        return MarketDataSourceFetchResult(
            source_name=self.source_name,
            requested_tickers=normalized_tickers,
            rows=tuple(rows),
            ticker_statuses=tuple(statuses),
        )


def build_market_data_source(source_name: str) -> MarketDataSource:
    normalized = source_name.strip().lower()
    if normalized == 'yahoo':
        return YahooMarketDataSource()
    raise ValueError(f'Unsupported market-data source: {source_name}')


def normalize_source_rows_for_v2(
    *,
    rows: Sequence[SourceMarketDataRow],
    created_at_utc: str,
    updated_at_utc: str,
) -> tuple['MarketDataRow', ...]:
    from tradetool.data.market_data_schema import MarketDataRow

    return tuple(
        MarketDataRow(
            ticker=row.ticker,
            price_date=row.price_date,
            raw_open=row.raw_open,
            raw_high=row.raw_high,
            raw_low=row.raw_low,
            raw_close=row.raw_close,
            adjusted_close=row.adjusted_close if row.adjusted_close is not None else (row.raw_close or 0.0),
            volume=0.0 if row.volume is None else row.volume,
            data_source=row.data_source,
            created_at_utc=created_at_utc,
            updated_at_utc=updated_at_utc,
        )
        for row in rows
    )


def _normalize_requested_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def _normalize_yahoo_row(
    *,
    ticker: str,
    index_value: object,
    row_mapping: Mapping[str, object],
) -> SourceMarketDataRow:
    price_date = _coerce_index_date(index_value)
    raw_close = _coerce_optional_float(_mapping_get(row_mapping, 'Close'))
    adjusted_close = _coerce_optional_float(_mapping_get(row_mapping, 'Adj Close'))
    warning_codes: list[str] = []
    if adjusted_close is None and raw_close is not None:
        adjusted_close = raw_close
        warning_codes.append('adjusted_close_fallback_to_raw_close')
    return SourceMarketDataRow(
        ticker=ticker,
        price_date=price_date,
        raw_open=_coerce_optional_float(_mapping_get(row_mapping, 'Open')),
        raw_high=_coerce_optional_float(_mapping_get(row_mapping, 'High')),
        raw_low=_coerce_optional_float(_mapping_get(row_mapping, 'Low')),
        raw_close=raw_close,
        adjusted_close=adjusted_close,
        volume=_coerce_optional_float(_mapping_get(row_mapping, 'Volume')),
        data_source='yahoo',
        warning_codes=tuple(sorted(set(warning_codes))),
    )


def _coerce_index_date(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _coerce_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        if value != value:
            return None
    except Exception:
        pass
    return float(value)


def _mapping_get(row_mapping: Mapping[object, object], label: str) -> object | None:
    if label in row_mapping:
        return row_mapping[label]
    for key, value in row_mapping.items():
        if isinstance(key, tuple) and key and key[0] == label:
            return value
    return None

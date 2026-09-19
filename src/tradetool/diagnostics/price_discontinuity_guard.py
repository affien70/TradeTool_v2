from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Mapping, Sequence

from tradetool.data import PriceHistoryV2Record

UPWARD_RATIO = 5.0
DOWNWARD_RATIO = 0.20


@dataclass(frozen=True, slots=True)
class PriceDiscontinuity:
    ticker: str
    previous_date: date
    current_date: date
    raw_ratio: float
    adjusted_ratio: float


def detect_price_discontinuities(
    rows_by_ticker: Mapping[str, Sequence[PriceHistoryV2Record]],
) -> tuple[PriceDiscontinuity, ...]:
    events: list[PriceDiscontinuity] = []
    for ticker in sorted(rows_by_ticker):
        rows = sorted(rows_by_ticker[ticker], key=lambda row: row.price_date)
        for previous, current in zip(rows, rows[1:]):
            if not _valid_prices(previous) or not _valid_prices(current):
                continue
            raw_ratio = current.raw_close / previous.raw_close
            adjusted_ratio = current.adjusted_close / previous.adjusted_close
            if _is_discontinuity(raw_ratio) or _is_discontinuity(adjusted_ratio):
                events.append(
                    PriceDiscontinuity(
                        ticker=ticker,
                        previous_date=previous.price_date,
                        current_date=current.price_date,
                        raw_ratio=raw_ratio,
                        adjusted_ratio=adjusted_ratio,
                    )
                )
    return tuple(events)


def index_price_discontinuities(
    events: Sequence[PriceDiscontinuity],
) -> Mapping[str, tuple[PriceDiscontinuity, ...]]:
    indexed: dict[str, list[PriceDiscontinuity]] = defaultdict(list)
    for event in events:
        indexed[event.ticker].append(event)
    return {
        ticker: tuple(sorted(ticker_events, key=lambda event: (event.previous_date, event.current_date)))
        for ticker, ticker_events in indexed.items()
    }


def holding_window_crosses_discontinuity(
    *,
    ticker: str,
    entry_date: date,
    exit_date: date,
    events_by_ticker: Mapping[str, Sequence[PriceDiscontinuity]],
) -> bool:
    return any(
        entry_date <= event.previous_date and event.current_date <= exit_date
        for event in events_by_ticker.get(ticker, ())
    )


def _valid_prices(row: PriceHistoryV2Record) -> bool:
    return all(
        isfinite(value) and value > 0.0
        for value in (row.raw_close, row.adjusted_close)
    )


def _is_discontinuity(ratio: float) -> bool:
    return ratio >= UPWARD_RATIO or ratio <= DOWNWARD_RATIO
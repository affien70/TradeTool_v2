"""Read-only V2 market-data adapter for pure Holdings signal inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from tradetool.data import ReadOnlySQLite, load_price_history_v2_for_tickers
from tradetool.features import (
    atr_trailing_stop,
    average_true_range,
    fast_sma_slope_positive,
    holdings_relative_strength,
    post_entry_peak,
    simple_moving_average,
    trailing_stop_breached,
)
from tradetool.features.raw import compute_raw_features
from tradetool.holdings.core import PositionState
from tradetool.holdings.signals import HoldingSignalInputs
from tradetool.holdings.storage import HoldingSettings


@dataclass(frozen=True, slots=True)
class HoldingMarketDataResult:
    ticker: str | None
    benchmark_id: str
    as_of_date: date | None
    ready: bool
    signal_inputs: HoldingSignalInputs | None
    unavailable_reasons: tuple[str, ...] = ()


def build_holding_signal_inputs_from_v2_price_history(
    *,
    db_path: str | Path,
    ticker: str | None,
    position: PositionState,
    settings: HoldingSettings,
    entry_date: date | datetime | str | None = None,
    data_source: str = 'yahoo',
    max_price_date: date | None = None,
    database: ReadOnlySQLite | None = None,
) -> HoldingMarketDataResult:
    """Load V2 price history and build only the explicit inputs consumed by H2b."""
    normalized_ticker = _normalized_ticker(ticker)
    benchmark_id = settings.norway_benchmark_id.strip().upper()
    if not normalized_ticker:
        return HoldingMarketDataResult(
            ticker=None,
            benchmark_id=benchmark_id,
            as_of_date=None,
            ready=False,
            signal_inputs=None,
            unavailable_reasons=('ticker_missing',),
        )

    loaded = load_price_history_v2_for_tickers(
        db_path=str(db_path),
        tickers=(normalized_ticker, benchmark_id),
        data_source=data_source,
        max_price_date=max_price_date,
        database=database,
    )
    stock_rows = loaded.rows_by_ticker.get(normalized_ticker, ())
    benchmark_rows = loaded.rows_by_ticker.get(benchmark_id, ())
    as_of_date = stock_rows[-1].price_date if stock_rows else None
    reasons: list[str] = []
    if not stock_rows:
        reasons.append('stock_market_data_missing')
    if not benchmark_rows:
        reasons.append('benchmark_market_data_missing')
    if reasons:
        return _unavailable(
            ticker=normalized_ticker,
            benchmark_id=benchmark_id,
            as_of_date=as_of_date,
            reasons=reasons,
        )

    closes = tuple(row.adjusted_close for row in stock_rows)
    benchmark_closes = tuple(row.adjusted_close for row in benchmark_rows)
    latest_close = closes[-1]
    fast_sma = None
    fast_sma_slope = None
    if settings.sell_fast_sma_days > 0:
        fast_sma = simple_moving_average(closes, window=settings.sell_fast_sma_days)
        fast_sma_slope = fast_sma_slope_positive(closes, window=settings.sell_fast_sma_days)
        if fast_sma is None:
            reasons.append('insufficient_fast_sma_history')
        if fast_sma_slope is None:
            reasons.append('insufficient_fast_sma_slope_history')

    long_sma = simple_moving_average(closes, window=200)
    if long_sma is None:
        reasons.append('insufficient_sma200_history')
    rs_value = holdings_relative_strength(closes, benchmark_closes, months=settings.rs_months)
    if rs_value is None:
        reasons.append('insufficient_rs_history')
    feature_result = compute_raw_features(tuple(row.to_feature_record() for row in stock_rows))
    short_term_return = feature_result.features['return_1m']
    if short_term_return is None:
        reasons.append('insufficient_momentum_history')

    trailing_stop_breach = False
    if settings.sell_drop_from_peak:
        entry = _coerce_date(entry_date)
        if entry is None:
            reasons.append('required_entry_metadata_unavailable')
        elif position.gav is None:
            reasons.append('required_entry_cost_unavailable')
        else:
            post_entry_rows = tuple(row for row in stock_rows if row.price_date >= entry)
            atr = average_true_range(
                tuple(row.high for row in post_entry_rows),
                tuple(row.low for row in post_entry_rows),
                tuple(row.raw_close for row in post_entry_rows),
            )
            if atr is None:
                reasons.append('insufficient_atr_history')
            else:
                trailing_stop = atr_trailing_stop(
                    close=latest_close,
                    entry_price=position.gav,
                    peak_price=post_entry_peak(tuple(row.adjusted_close for row in post_entry_rows)),
                    atr=atr,
                    atr_multiplier=settings.atr_multiplier,
                )
                trailing_stop_breach = trailing_stop_breached(close=latest_close, trailing_stop=trailing_stop)

    if reasons:
        return _unavailable(
            ticker=normalized_ticker,
            benchmark_id=benchmark_id,
            as_of_date=as_of_date,
            reasons=reasons,
        )

    known_cost_basis = position.cost_basis_status in {'known', 'partially_unknown'} and position.gav is not None
    return HoldingMarketDataResult(
        ticker=normalized_ticker,
        benchmark_id=benchmark_id,
        as_of_date=as_of_date,
        ready=True,
        signal_inputs=HoldingSignalInputs(
            below_fast_sma=bool(fast_sma is not None and latest_close < fast_sma),
            weak_rs=bool(rs_value is not None and rs_value < settings.rs_threshold),
            below_cost_basis=bool(known_cost_basis and latest_close <= position.gav * 0.90),
            drop_from_peak=trailing_stop_breach,
            below_long_sma=bool(long_sma is not None and latest_close < long_sma),
            short_term_return=float(short_term_return),
            fast_sma_slope_positive=fast_sma_slope,
        ),
    )


def _unavailable(
    *,
    ticker: str,
    benchmark_id: str,
    as_of_date: date | None,
    reasons: list[str],
) -> HoldingMarketDataResult:
    return HoldingMarketDataResult(
        ticker=ticker,
        benchmark_id=benchmark_id,
        as_of_date=as_of_date,
        ready=False,
        signal_inputs=None,
        unavailable_reasons=tuple(dict.fromkeys(reasons)),
    )


def _normalized_ticker(value: str | None) -> str | None:
    normalized = str(value or '').strip().upper()
    return normalized or None


def _coerce_date(value: date | datetime | str | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None
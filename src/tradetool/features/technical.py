"""Pure technical calculations shared by market-data consumers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite


TRADING_DAYS_PER_MONTH = 21
FAST_SMA_SLOPE_COMPARISON_POINTS = 20
ATR_WINDOW = 14
TRAILING_STOP_ACTIVATION_GAIN_PCT = 0.05
TRAILING_STOP_MINIMUM_PCT = 0.10


@dataclass(frozen=True, slots=True)
class AtrTrailingStop:
    atr_pct: float | None
    stop_pct: float | None
    stop_price: float | None
    active: bool


def holdings_relative_strength(
    asset_closes: Sequence[float | int | None],
    benchmark_closes: Sequence[float | int | None],
    *,
    months: int = 6,
) -> float | None:
    """Return V1 Holdings ratio RS from each series' own terminal observations."""
    days = months_to_trading_days(months)
    asset_values = _finite_values(asset_closes)
    benchmark_values = _finite_values(benchmark_closes)
    if len(asset_values) < days + 5 or len(benchmark_values) < days + 5:
        return None
    asset_base = asset_values[-days]
    benchmark_base = benchmark_values[-days]
    if asset_base == 0 or benchmark_base == 0:
        return None
    asset_return = asset_values[-1] / asset_base
    benchmark_return = benchmark_values[-1] / benchmark_base
    if not isfinite(asset_return) or not isfinite(benchmark_return) or benchmark_return == 0:
        return None
    return asset_return / benchmark_return


def months_to_trading_days(months: int) -> int:
    """Map V1's supported month labels to trading days, preserving its fallback."""
    return {1: 21, 3: 63, 6: 126, 12: 252}.get(int(months), int(months) * TRADING_DAYS_PER_MONTH)


def simple_moving_average(values: Sequence[float | int | None], *, window: int) -> float | None:
    """Return the latest simple moving average, or None without a full finite window."""
    _require_positive_window(window)
    if len(values) < window:
        return None
    selected = values[-window:]
    if not _all_finite(selected):
        return None
    return sum(float(value) for value in selected) / window


def fast_sma_slope_positive(
    closes: Sequence[float | int | None],
    *,
    window: int,
) -> bool | None:
    """Return V1's fast-SMA rising state from the last and twentieth valid SMA."""
    _require_positive_window(window)
    averages = [
        simple_moving_average(closes[:index], window=window)
        for index in range(window, len(closes) + 1)
    ]
    valid_averages = [average for average in averages if average is not None]
    if len(valid_averages) < FAST_SMA_SLOPE_COMPARISON_POINTS:
        return None
    previous = valid_averages[-FAST_SMA_SLOPE_COMPARISON_POINTS]
    latest = valid_averages[-1]
    if previous <= 0:
        return None
    return latest > previous


def average_true_range(
    highs: Sequence[float | int | None],
    lows: Sequence[float | int | None],
    closes: Sequence[float | int | None],
    *,
    window: int = ATR_WINDOW,
) -> float | None:
    """Return V1's latest ATR: a simple rolling mean of true range."""
    _require_positive_window(window)
    if len(highs) != len(lows) or len(highs) != len(closes) or len(closes) < window:
        return None
    true_ranges: list[float] = []
    for index, (high, low, close) in enumerate(zip(highs, lows, closes, strict=True)):
        components: list[float] = []
        if _is_finite(high) and _is_finite(low):
            components.append(float(high) - float(low))
        if index > 0 and _is_finite(closes[index - 1]):
            previous_close = float(closes[index - 1])
            if _is_finite(high):
                components.append(abs(float(high) - previous_close))
            if _is_finite(low):
                components.append(abs(float(low) - previous_close))
        true_ranges.append(max(components) if components else float('nan'))
    selected = true_ranges[-window:]
    if not _all_finite(selected):
        return None
    return sum(selected) / window


def post_entry_peak(closes: Sequence[float | int | None]) -> float | None:
    """Return the V1 post-entry closing-price peak from finite observations."""
    values = _finite_values(closes)
    return max(values) if values else None


def atr_trailing_stop(
    *,
    close: float | int | None,
    entry_price: float | int | None,
    peak_price: float | int | None,
    atr: float | int | None,
    atr_multiplier: float = 2.5,
    activation_gain_pct: float = TRAILING_STOP_ACTIVATION_GAIN_PCT,
    minimum_stop_pct: float = TRAILING_STOP_MINIMUM_PCT,
) -> AtrTrailingStop:
    """Return the V1 Holdings ATR trailing-stop state without making a signal decision."""
    required_values = (close, entry_price, peak_price, atr)
    if not _all_finite(required_values):
        return AtrTrailingStop(None, None, None, False)
    close_value, entry_value, peak_value, atr_value = (float(value) for value in required_values)
    if entry_value <= 0 or peak_value <= 0 or atr_value <= 0:
        return AtrTrailingStop(None, None, None, False)
    atr_pct = atr_value / peak_value
    stop_pct = max(float(minimum_stop_pct), float(atr_multiplier) * atr_pct)
    active = (peak_value / entry_value) - 1.0 >= float(activation_gain_pct)
    if not active:
        return AtrTrailingStop(atr_pct, stop_pct, None, False)
    return AtrTrailingStop(atr_pct, stop_pct, peak_value * (1.0 - stop_pct), True)


def trailing_stop_breached(*, close: float | int | None, trailing_stop: AtrTrailingStop) -> bool:
    """Return V1's inclusive close-versus-active-stop breach state."""
    return bool(
        trailing_stop.active
        and trailing_stop.stop_price is not None
        and _is_finite(close)
        and float(close) <= trailing_stop.stop_price
    )


def _finite_values(values: Sequence[float | int | None]) -> list[float]:
    return [float(value) for value in values if _is_finite(value)]


def _all_finite(values: Sequence[float | int | None]) -> bool:
    return all(_is_finite(value) for value in values)


def _is_finite(value: float | int | None) -> bool:
    return value is not None and isfinite(float(value))


def _require_positive_window(window: int) -> None:
    if window <= 0:
        raise ValueError('window must be positive.')
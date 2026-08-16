from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import math

from tradetool.data import PriceHistoryRecord

FEATURE_REASON_INSUFFICIENT_12M = 'insufficient_rows_for_12m_return'
FEATURE_REASON_MISSING_BENCHMARK = 'missing_benchmark_data'
FEATURE_REASON_BENCHMARK_ALIGNMENT_FAILED = 'benchmark_alignment_failed'
FEATURE_REASON_MISSING_OHLCV_INPUT = 'missing_ohlcv_input'
FEATURE_REASON_INVALID_COMPUTED_FEATURE = 'invalid_computed_feature_value'

LOOKBACK_1M = 21
LOOKBACK_3M = 63
LOOKBACK_6M = 126
LOOKBACK_12M = 252


@dataclass(frozen=True, slots=True)
class FeatureComputationResult:
    ticker: str
    feature_date: date
    feature_complete: bool
    feature_missing_reasons: tuple[str, ...]
    features: Mapping[str, float | int | bool | str | None]
    benchmark_ticker: str | None
    benchmark_alignment_status: str


def compute_raw_features(
    rows: Sequence[PriceHistoryRecord],
    *,
    benchmark_rows: Sequence[PriceHistoryRecord] | None = None,
    benchmark_ticker: str | None = None,
) -> FeatureComputationResult:
    if not rows:
        raise ValueError('Cannot compute raw features without any price rows.')
    ordered_rows = tuple(sorted(rows, key=lambda row: row.price_date))
    latest_row = ordered_rows[-1]
    missing_reasons: list[str] = []

    if any(_row_has_missing_ohlcv(row) for row in ordered_rows):
        missing_reasons.append(FEATURE_REASON_MISSING_OHLCV_INPUT)

    closes = [row.close for row in ordered_rows]
    volumes = [row.volume for row in ordered_rows]
    features: dict[str, float | int | bool | str | None] = {
        'latest_close': latest_row.close,
        'latest_price_date': latest_row.price_date.isoformat(),
        'row_count': len(ordered_rows),
        'return_1m': _compute_return(closes, LOOKBACK_1M),
        'return_3m': _compute_return(closes, LOOKBACK_3M),
        'return_6m': _compute_return(closes, LOOKBACK_6M),
        'return_12m': _compute_return(closes, LOOKBACK_12M),
        'sma50': _compute_sma(closes, 50),
        'sma100': _compute_sma(closes, 100),
        'sma200': _compute_sma(closes, 200),
        'drawdown_252': _compute_drawdown(closes, LOOKBACK_12M),
        'volatility_63': _compute_volatility(closes, LOOKBACK_3M),
        'volatility_126': _compute_volatility(closes, LOOKBACK_6M),
        'average_volume_20': _compute_average(volumes, 20),
        'average_volume_63': _compute_average(volumes, LOOKBACK_3M),
        'average_traded_value_20': _compute_average([row.close * row.volume for row in ordered_rows], 20),
        'average_traded_value_63': _compute_average([row.close * row.volume for row in ordered_rows], LOOKBACK_3M),
    }
    features['distance_to_sma50'] = _compute_distance(features['latest_close'], features['sma50'])
    features['distance_to_sma200'] = _compute_distance(features['latest_close'], features['sma200'])
    features['above_sma50'] = _compute_above(features['latest_close'], features['sma50'])
    features['above_sma100'] = _compute_above(features['latest_close'], features['sma100'])
    features['above_sma200'] = _compute_above(features['latest_close'], features['sma200'])

    if features['return_12m'] is None:
        missing_reasons.append(FEATURE_REASON_INSUFFICIENT_12M)

    benchmark_alignment_status = 'not_requested'
    if benchmark_ticker is not None:
        if not benchmark_rows:
            raise ValueError(f'Benchmark ticker "{benchmark_ticker}" is missing from price history.')
        benchmark_alignment_status, benchmark_features, benchmark_reasons = _compute_benchmark_context(
            stock_rows=ordered_rows,
            benchmark_rows=tuple(sorted(benchmark_rows, key=lambda row: row.price_date)),
        )
        features.update(benchmark_features)
        missing_reasons.extend(benchmark_reasons)
    else:
        features.update(
            {
                'benchmark_return_1m': None,
                'benchmark_return_3m': None,
                'benchmark_return_6m': None,
                'benchmark_return_12m': None,
                'relative_strength_1m': None,
                'relative_strength_3m': None,
                'relative_strength_6m': None,
                'relative_strength_12m': None,
            }
        )

    if any(_is_invalid_feature_value(value) for value in features.values()):
        missing_reasons.append(FEATURE_REASON_INVALID_COMPUTED_FEATURE)

    deduped_reasons = tuple(dict.fromkeys(missing_reasons))
    return FeatureComputationResult(
        ticker=latest_row.ticker,
        feature_date=latest_row.price_date,
        feature_complete=not deduped_reasons,
        feature_missing_reasons=deduped_reasons,
        features=features,
        benchmark_ticker=benchmark_ticker,
        benchmark_alignment_status=benchmark_alignment_status,
    )


def compute_feature_readiness(
    rows_by_ticker: Mapping[str, Sequence[PriceHistoryRecord]],
    *,
    benchmark_ticker: str | None = None,
) -> tuple[FeatureComputationResult, ...]:
    benchmark_rows = None if benchmark_ticker is None else rows_by_ticker.get(benchmark_ticker)
    results: list[FeatureComputationResult] = []
    for ticker in sorted(rows_by_ticker):
        if benchmark_ticker is not None and ticker == benchmark_ticker:
            continue
        results.append(
            compute_raw_features(
                rows_by_ticker[ticker],
                benchmark_rows=benchmark_rows,
                benchmark_ticker=benchmark_ticker,
            )
        )
    return tuple(results)


def _compute_benchmark_context(
    *,
    stock_rows: Sequence[PriceHistoryRecord],
    benchmark_rows: Sequence[PriceHistoryRecord],
) -> tuple[str, Mapping[str, float | None], list[str]]:
    latest_stock_date = stock_rows[-1].price_date
    benchmark_by_date = {row.price_date: row for row in benchmark_rows}
    if latest_stock_date not in benchmark_by_date:
        return (
            'alignment_failed',
            {
                'benchmark_return_1m': None,
                'benchmark_return_3m': None,
                'benchmark_return_6m': None,
                'benchmark_return_12m': None,
                'relative_strength_1m': None,
                'relative_strength_3m': None,
                'relative_strength_6m': None,
                'relative_strength_12m': None,
            },
            [FEATURE_REASON_BENCHMARK_ALIGNMENT_FAILED],
        )
    aligned_rows = tuple(row for row in benchmark_rows if row.price_date <= latest_stock_date)
    benchmark_closes = [row.close for row in aligned_rows]
    benchmark_returns = {
        'benchmark_return_1m': _compute_return(benchmark_closes, LOOKBACK_1M),
        'benchmark_return_3m': _compute_return(benchmark_closes, LOOKBACK_3M),
        'benchmark_return_6m': _compute_return(benchmark_closes, LOOKBACK_6M),
        'benchmark_return_12m': _compute_return(benchmark_closes, LOOKBACK_12M),
    }
    if any(value is None for value in benchmark_returns.values()):
        reasons = [FEATURE_REASON_MISSING_BENCHMARK]
    else:
        reasons = []
    stock_returns = {
        'return_1m': _compute_return([row.close for row in stock_rows], LOOKBACK_1M),
        'return_3m': _compute_return([row.close for row in stock_rows], LOOKBACK_3M),
        'return_6m': _compute_return([row.close for row in stock_rows], LOOKBACK_6M),
        'return_12m': _compute_return([row.close for row in stock_rows], LOOKBACK_12M),
    }
    return (
        'aligned',
        {
            **benchmark_returns,
            'relative_strength_1m': _subtract_or_none(stock_returns['return_1m'], benchmark_returns['benchmark_return_1m']),
            'relative_strength_3m': _subtract_or_none(stock_returns['return_3m'], benchmark_returns['benchmark_return_3m']),
            'relative_strength_6m': _subtract_or_none(stock_returns['return_6m'], benchmark_returns['benchmark_return_6m']),
            'relative_strength_12m': _subtract_or_none(stock_returns['return_12m'], benchmark_returns['benchmark_return_12m']),
        },
        reasons,
    )


def _compute_return(closes: Sequence[float], lookback: int) -> float | None:
    if len(closes) < lookback:
        return None
    base_value = closes[-lookback]
    latest_value = closes[-1]
    if base_value == 0:
        return None
    return (latest_value / base_value) - 1.0


def _compute_sma(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    selected = values[-window:]
    return sum(selected) / window


def _compute_drawdown(closes: Sequence[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    selected = closes[-window:]
    peak = max(selected)
    if peak == 0:
        return None
    trough = min(selected)
    return (trough / peak) - 1.0


def _compute_volatility(closes: Sequence[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    selected = closes[-window:]
    returns = [(selected[index] / selected[index - 1]) - 1.0 for index in range(1, len(selected)) if selected[index - 1] != 0]
    if not returns:
        return None
    mean_return = sum(returns) / len(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / len(returns)
    return math.sqrt(variance)


def _compute_average(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    selected = values[-window:]
    return sum(selected) / window


def _compute_distance(latest_close: float | int | bool | str | None, sma_value: float | int | bool | str | None) -> float | None:
    if not isinstance(latest_close, (int, float)) or not isinstance(sma_value, (int, float)) or sma_value == 0:
        return None
    return (float(latest_close) / float(sma_value)) - 1.0


def _compute_above(latest_close: float | int | bool | str | None, sma_value: float | int | bool | str | None) -> bool | None:
    if not isinstance(latest_close, (int, float)) or not isinstance(sma_value, (int, float)):
        return None
    return float(latest_close) > float(sma_value)


def _subtract_or_none(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _row_has_missing_ohlcv(row: PriceHistoryRecord) -> bool:
    return any(math.isnan(value) for value in (row.open, row.high, row.low, row.close, row.volume))


def _is_invalid_feature_value(value: object) -> bool:
    return isinstance(value, float) and not math.isfinite(value)

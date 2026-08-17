from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from pathlib import Path

from tradetool.data import ReadOnlySQLite, load_price_history_for_tickers
from tradetool.diagnostics.candidate_type import build_candidate_type_diagnostics
from tradetool.diagnostics.eligibility import build_eligibility_diagnostics
from tradetool.diagnostics.feature_readiness import build_feature_readiness_diagnostics


@dataclass(frozen=True, slots=True)
class CandidateSignalMatrixRow:
    candidate_type: str
    trade_signal: str
    count: int

    def to_dict(self) -> dict[str, object]:
        return {
            'candidate_type': self.candidate_type,
            'trade_signal': self.trade_signal,
            'count': self.count,
        }


@dataclass(frozen=True, slots=True)
class MinimalScreenerTableRow:
    raw_rank: int
    ticker: str
    raw_score: float
    trade_signal: str
    candidate_type: str
    latest_close: float | int | bool | str | None
    latest_price_date: str | None
    return_1m: float | int | bool | str | None
    return_3m: float | int | bool | str | None
    return_6m: float | int | bool | str | None
    return_12m: float | int | bool | str | None
    relative_strength_1m: float | int | bool | str | None
    relative_strength_3m: float | int | bool | str | None
    relative_strength_6m: float | int | bool | str | None
    relative_strength_12m: float | int | bool | str | None
    above_sma50: bool
    above_sma100: bool | None
    above_sma200: bool
    drawdown_252: float
    volatility_63: float
    average_traded_value_20: float
    distance_to_sma50: float | int | bool | str | None
    distance_to_sma200: float | int | bool | str | None
    policy_reasons: tuple[str, ...]
    policy_warnings: tuple[str, ...]
    classification_reasons: tuple[str, ...]
    classification_warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            'raw_rank': self.raw_rank,
            'ticker': self.ticker,
            'raw_score': self.raw_score,
            'trade_signal': self.trade_signal,
            'candidate_type': self.candidate_type,
            'latest_close': self.latest_close,
            'latest_price_date': self.latest_price_date,
            'return_1m': self.return_1m,
            'return_3m': self.return_3m,
            'return_6m': self.return_6m,
            'return_12m': self.return_12m,
            'relative_strength_1m': self.relative_strength_1m,
            'relative_strength_3m': self.relative_strength_3m,
            'relative_strength_6m': self.relative_strength_6m,
            'relative_strength_12m': self.relative_strength_12m,
            'above_sma50': self.above_sma50,
            'above_sma100': self.above_sma100,
            'above_sma200': self.above_sma200,
            'drawdown_252': self.drawdown_252,
            'volatility_63': self.volatility_63,
            'average_traded_value_20': self.average_traded_value_20,
            'distance_to_sma50': self.distance_to_sma50,
            'distance_to_sma200': self.distance_to_sma200,
            'policy_reasons': ', '.join(self.policy_reasons),
            'policy_warnings': ', '.join(self.policy_warnings),
            'classification_reasons': ', '.join(self.classification_reasons),
            'classification_warnings': ', '.join(self.classification_warnings),
        }


@dataclass(frozen=True, slots=True)
class MinimalScreenerResult:
    universe_id: str
    universe_source: str
    benchmark_ticker: str | None
    ranking_engine_id: str
    policy_engine_id: str
    classification_engine_id: str
    input_universe_count: int
    structural_eligible_count: int
    structural_rejected_count: int
    feature_complete_count: int
    feature_incomplete_count: int
    ranked_count: int
    trade_signal_counts: Mapping[str, int]
    candidate_type_counts: Mapping[str, int]
    structural_rejection_counts_by_reason: Mapping[str, int]
    feature_missing_reason_counts: Mapping[str, int]
    signal_type_matrix: tuple[CandidateSignalMatrixRow, ...]
    rows: tuple[MinimalScreenerTableRow, ...]

    def visible_rows(self, *, include_avoid: bool = False) -> tuple[MinimalScreenerTableRow, ...]:
        if include_avoid:
            return self.rows
        return tuple(row for row in self.rows if row.trade_signal != 'AVOID')


@dataclass(frozen=True, slots=True)
class SelectedTickerDetail:
    ticker: str
    raw_rank: int
    raw_score: float
    trade_signal: str
    candidate_type: str
    latest_close: float | int | bool | str | None
    latest_price_date: str | None
    policy_reasons: tuple[str, ...]
    policy_warnings: tuple[str, ...]
    classification_reasons: tuple[str, ...]
    classification_warnings: tuple[str, ...]
    metrics: Mapping[str, float | int | bool | str | None]


@dataclass(frozen=True, slots=True)
class ScreenerChartPoint:
    price_date: str
    close: float
    sma50: float | None
    sma200: float | None
    benchmark_close: float | None
    indexed_close: float | None
    indexed_benchmark: float | None
    relative_strength_line: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            'price_date': self.price_date,
            'close': self.close,
            'sma50': self.sma50,
            'sma200': self.sma200,
            'benchmark_close': self.benchmark_close,
            'indexed_close': self.indexed_close,
            'indexed_benchmark': self.indexed_benchmark,
            'relative_strength_line': self.relative_strength_line,
        }


@dataclass(frozen=True, slots=True)
class SelectedTickerChartDetail:
    ticker: str
    benchmark_ticker: str | None
    lookback_rows: int
    price_points: tuple[ScreenerChartPoint, ...]
    loaded_tickers: tuple[str, ...]
    warning: str | None = None


def build_minimal_screener_result(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str | None = None,
    price_table: str = 'price_history',
    min_history_rows: int = 252,
    freshness_tolerance_days: int = 0,
) -> MinimalScreenerResult:
    eligibility = build_eligibility_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    feature_readiness = build_feature_readiness_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        benchmark_ticker=benchmark_ticker,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    candidate = build_candidate_type_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        benchmark_ticker=benchmark_ticker,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    rows = _build_table_rows(candidate)
    return MinimalScreenerResult(
        universe_id=universe_id,
        universe_source=candidate.policy.ranking.universe_source,
        benchmark_ticker=benchmark_ticker,
        ranking_engine_id=candidate.policy.ranking.ranking_engine_id,
        policy_engine_id=candidate.policy.policy_engine_id,
        classification_engine_id=candidate.classification_engine_id,
        input_universe_count=eligibility.input_universe_count,
        structural_eligible_count=eligibility.eligible_count,
        structural_rejected_count=eligibility.rejected_count,
        feature_complete_count=feature_readiness.feature_complete_count,
        feature_incomplete_count=feature_readiness.feature_incomplete_count,
        ranked_count=candidate.policy.ranking.ranked_count,
        trade_signal_counts=candidate.trade_signal_counts,
        candidate_type_counts=candidate.candidate_type_counts,
        structural_rejection_counts_by_reason=eligibility.rejection_counts_by_reason,
        feature_missing_reason_counts=feature_readiness.feature_missing_reason_counts,
        signal_type_matrix=_build_signal_type_matrix(candidate.rows),
        rows=rows,
    )


def _build_table_rows(candidate) -> tuple[MinimalScreenerTableRow, ...]:
    rows: list[MinimalScreenerTableRow] = []
    for candidate_row, policy_row, ranking_row in zip(
        candidate.rows,
        candidate.policy.rows,
        candidate.policy.ranking.rows,
        strict=True,
    ):
        if (
            candidate_row.ticker != policy_row.ticker or
            candidate_row.ticker != ranking_row.ticker or
            candidate_row.raw_rank != policy_row.raw_rank or
            candidate_row.raw_rank != ranking_row.raw_rank
        ):
            raise ValueError('Candidate-type rows, policy rows, and ranking rows are not aligned.')
        features = ranking_row.input_fields
        rows.append(
            MinimalScreenerTableRow(
                raw_rank=candidate_row.raw_rank,
                ticker=candidate_row.ticker,
                raw_score=candidate_row.raw_score,
                trade_signal=candidate_row.trade_signal.value,
                candidate_type=candidate_row.candidate_type.value,
                latest_close=features.get('latest_close'),
                latest_price_date=_as_optional_str(features.get('latest_price_date')),
                return_1m=features.get('return_1m'),
                return_3m=features.get('return_3m'),
                return_6m=features.get('return_6m'),
                return_12m=features.get('return_12m'),
                relative_strength_1m=features.get('relative_strength_1m'),
                relative_strength_3m=features.get('relative_strength_3m'),
                relative_strength_6m=features.get('relative_strength_6m'),
                relative_strength_12m=features.get('relative_strength_12m'),
                above_sma50=policy_row.above_sma50,
                above_sma100=features.get('above_sma100'),
                above_sma200=policy_row.above_sma200,
                drawdown_252=policy_row.drawdown_252,
                volatility_63=policy_row.volatility_63,
                average_traded_value_20=policy_row.average_traded_value_20,
                distance_to_sma50=features.get('distance_to_sma50'),
                distance_to_sma200=features.get('distance_to_sma200'),
                policy_reasons=policy_row.policy_reasons,
                policy_warnings=policy_row.policy_warnings,
                classification_reasons=candidate_row.classification_reasons,
                classification_warnings=candidate_row.classification_warnings,
            )
        )
    return tuple(rows)


def build_selected_ticker_detail(
    result: MinimalScreenerResult,
    *,
    ticker: str,
    include_avoid: bool = False,
) -> SelectedTickerDetail:
    rows = result.visible_rows(include_avoid=include_avoid)
    selected = next((row for row in rows if row.ticker == ticker), None)
    if selected is None:
        raise ValueError(f'Ticker not found in current screener result: {ticker}')
    metrics = {
        'return_1m': selected.return_1m,
        'return_3m': selected.return_3m,
        'return_6m': selected.return_6m,
        'return_12m': selected.return_12m,
        'relative_strength_1m': selected.relative_strength_1m,
        'relative_strength_3m': selected.relative_strength_3m,
        'relative_strength_6m': selected.relative_strength_6m,
        'relative_strength_12m': selected.relative_strength_12m,
        'above_sma50': selected.above_sma50,
        'above_sma100': selected.above_sma100,
        'above_sma200': selected.above_sma200,
        'drawdown_252': selected.drawdown_252,
        'volatility_63': selected.volatility_63,
        'average_traded_value_20': selected.average_traded_value_20,
        'distance_to_sma50': selected.distance_to_sma50,
        'distance_to_sma200': selected.distance_to_sma200,
    }
    return SelectedTickerDetail(
        ticker=selected.ticker,
        raw_rank=selected.raw_rank,
        raw_score=selected.raw_score,
        trade_signal=selected.trade_signal,
        candidate_type=selected.candidate_type,
        latest_close=selected.latest_close,
        latest_price_date=selected.latest_price_date,
        policy_reasons=selected.policy_reasons,
        policy_warnings=selected.policy_warnings,
        classification_reasons=selected.classification_reasons,
        classification_warnings=selected.classification_warnings,
        metrics=metrics,
    )


def build_selected_ticker_chart_detail(
    *,
    db_path: str | Path,
    ticker: str,
    benchmark_ticker: str | None,
    lookback_rows: int = 252,
    price_table: str = 'price_history',
) -> SelectedTickerChartDetail:
    database = ReadOnlySQLite(db_path)
    tickers = [ticker]
    if benchmark_ticker:
        tickers.append(benchmark_ticker)
    loaded = load_price_history_for_tickers(database=database, tickers=tickers, price_table=price_table)
    ticker_rows = list(loaded.rows_by_ticker.get(ticker.upper(), ()))
    if not ticker_rows:
        raise ValueError(f'Mangler prisdata for valgt ticker: {ticker}')
    limited_rows = ticker_rows[-lookback_rows:]
    benchmark_rows = list(loaded.rows_by_ticker.get(benchmark_ticker.upper(), ())) if benchmark_ticker else []
    benchmark_by_date = {row.price_date.isoformat(): row.close for row in benchmark_rows}

    if benchmark_ticker and not benchmark_rows:
        return SelectedTickerChartDetail(
            ticker=ticker.upper(),
            benchmark_ticker=benchmark_ticker,
            lookback_rows=lookback_rows,
            price_points=_build_price_points(limited_rows, benchmark_by_date={}),
            loaded_tickers=tuple(sorted(loaded.rows_by_ticker)),
            warning=f'Mangler benchmark-data for {benchmark_ticker}. Viser kun prisserien for valgt ticker.',
        )

    aligned_dates = [row.price_date.isoformat() for row in limited_rows if not benchmark_ticker or row.price_date.isoformat() in benchmark_by_date]
    if benchmark_ticker and not aligned_dates:
        return SelectedTickerChartDetail(
            ticker=ticker.upper(),
            benchmark_ticker=benchmark_ticker,
            lookback_rows=lookback_rows,
            price_points=_build_price_points(limited_rows, benchmark_by_date={}),
            loaded_tickers=tuple(sorted(loaded.rows_by_ticker)),
            warning=f'Kunne ikke justere datoer mellom {ticker.upper()} og {benchmark_ticker}.',
        )

    if benchmark_ticker:
        filtered_rows = [row for row in limited_rows if row.price_date.isoformat() in set(aligned_dates)]
        filtered_benchmark = {date_key: benchmark_by_date[date_key] for date_key in aligned_dates}
    else:
        filtered_rows = limited_rows
        filtered_benchmark = {}

    return SelectedTickerChartDetail(
        ticker=ticker.upper(),
        benchmark_ticker=benchmark_ticker,
        lookback_rows=lookback_rows,
        price_points=_build_price_points(filtered_rows, benchmark_by_date=filtered_benchmark),
        loaded_tickers=tuple(sorted(loaded.rows_by_ticker)),
        warning=None,
    )


def _build_signal_type_matrix(rows: Sequence) -> tuple[CandidateSignalMatrixRow, ...]:
    matrix: Counter[tuple[str, str]] = Counter()
    for row in rows:
        matrix[(row.candidate_type.value, row.trade_signal.value)] += 1
    return tuple(
        CandidateSignalMatrixRow(candidate_type=candidate_type, trade_signal=trade_signal, count=count)
        for (candidate_type, trade_signal), count in sorted(matrix.items())
    )


def _as_optional_str(value: float | int | bool | str | None) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _build_price_points(rows, *, benchmark_by_date: Mapping[str, float]) -> tuple[ScreenerChartPoint, ...]:
    closes = [float(row.close) for row in rows]
    stock_indexed = _indexed_series(closes)
    benchmark_closes = [benchmark_by_date.get(row.price_date.isoformat()) for row in rows]
    benchmark_indexed = _indexed_series([value for value in benchmark_closes if value is not None]) if benchmark_by_date else []
    benchmark_index_lookup: dict[str, float] = {}
    if benchmark_by_date:
        aligned_dates = [row.price_date.isoformat() for row in rows]
        values = [benchmark_by_date[date_key] for date_key in aligned_dates]
        indexed = _indexed_series(values)
        benchmark_index_lookup = {date_key: indexed_value for date_key, indexed_value in zip(aligned_dates, indexed, strict=True)}

    points: list[ScreenerChartPoint] = []
    for index, row in enumerate(rows):
        date_key = row.price_date.isoformat()
        sma50 = _rolling_mean(closes, index, 50)
        sma200 = _rolling_mean(closes, index, 200)
        indexed_close = stock_indexed[index] if index < len(stock_indexed) else None
        indexed_benchmark = benchmark_index_lookup.get(date_key)
        relative_strength_line = None
        if indexed_close is not None and indexed_benchmark not in (None, 0):
            relative_strength_line = (indexed_close / indexed_benchmark) * 100.0
        points.append(
            ScreenerChartPoint(
                price_date=date_key,
                close=float(row.close),
                sma50=sma50,
                sma200=sma200,
                benchmark_close=benchmark_by_date.get(date_key),
                indexed_close=indexed_close,
                indexed_benchmark=indexed_benchmark,
                relative_strength_line=relative_strength_line,
            )
        )
    return tuple(points)


def _rolling_mean(values: Sequence[float], end_index: int, window: int) -> float | None:
    start_index = end_index - window + 1
    if start_index < 0:
        return None
    window_values = values[start_index:end_index + 1]
    return sum(window_values) / len(window_values)


def _indexed_series(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    base = values[0]
    if base == 0 or math.isnan(base):
        return []
    return [(value / base) * 100.0 for value in values]

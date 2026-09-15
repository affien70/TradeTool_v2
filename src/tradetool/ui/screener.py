from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from pathlib import Path

from tradetool.data import ReadOnlySQLite, load_price_history_for_tickers, load_price_history_v2_for_tickers
from tradetool.diagnostics.candidate_type import build_candidate_type_diagnostics
from tradetool.diagnostics.eligibility import build_eligibility_diagnostics
from tradetool.diagnostics.feature_readiness import build_feature_readiness_diagnostics
from tradetool.diagnostics.market_data_v2_readiness import build_market_data_v2_readiness
from tradetool.policy import (
    CANDIDATE_TYPE_ENGINE_ID,
    CandidateTypeInputRow,
    TRADE_POLICY_ENGINE_ID,
    TradePolicyInputRow,
    apply_candidate_type_diagnostics,
    apply_trade_policy_diagnostics,
    summarize_candidate_types,
)
from tradetool.ranking import BASELINE_RANKING_ENGINE_ID, BaselineRankingInput, build_baseline_ranking
from tradetool.ui.v1_screener_adapter import (
    resolve_v1_selected_ticker,
    select_v1_candidate,
    v1_candidate_detail_rows,
    v1_candidate_explanation,
    v1_screener_eligible_rows,
    v1_screener_table_rows,
    v1_screener_ticker_options,
    v1_summary_rows,
)

PRICE_TABLE_LEGACY = 'price_history'
PRICE_TABLE_V2 = 'price_history_v2'


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
    price_table: str
    data_source: str | None
    close_input_source: str
    benchmark_alignment_date: str | None
    benchmark_lag_warning_count: int
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


def build_incumbent_screener_ui_result(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str,
    as_of_date,
    data_source: str,
    top_n: int = 10,
    universe_db_path: str | Path | None = None,
):
    from tradetool.ranking.incumbent_screener import build_incumbent_screener

    kwargs = {
        'db_path': db_path,
        'universe_id': universe_id,
        'benchmark_ticker': benchmark_ticker,
        'as_of_date': as_of_date,
        'data_source': data_source,
        'top_n': top_n,
    }
    if universe_db_path is not None:
        kwargs['universe_db_path'] = universe_db_path
    return build_incumbent_screener(**kwargs)


def incumbent_screener_table_rows(result) -> list[dict[str, object]]:
    return v1_screener_table_rows(result)


def incumbent_screener_eligible_table_rows(result) -> list[dict[str, object]]:
    return v1_screener_eligible_rows(result)


def selected_incumbent_candidate(result, *, ticker: str) -> Mapping[str, object]:
    return select_v1_candidate(result, ticker=ticker)


def incumbent_candidate_detail_rows(row: Mapping[str, object]) -> list[dict[str, object]]:
    return v1_candidate_detail_rows(row)


def incumbent_candidate_explanation(row: Mapping[str, object]) -> str:
    return v1_candidate_explanation(row)


def incumbent_screener_summary_rows(result) -> list[dict[str, object]]:
    return v1_summary_rows(result)


def incumbent_screener_ticker_options(table_rows: Sequence[Mapping[str, object]]) -> list[str]:
    return v1_screener_ticker_options(table_rows)


def resolve_incumbent_selected_ticker(
    table_rows: Sequence[Mapping[str, object]],
    *,
    current_ticker: str = '',
    selected_row_indexes: Sequence[int] | None = None,
    selected_ticker: str = '',
) -> str:
    return resolve_v1_selected_ticker(
        table_rows,
        current_ticker=current_ticker,
        selected_row_indexes=selected_row_indexes,
        selected_ticker=selected_ticker,
    )


def build_minimal_screener_result(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str | None = None,
    explicit_tickers: Sequence[str] | None = None,
    price_table: str = 'price_history',
    data_source: str = 'yahoo',
    min_history_rows: int = 252,
    freshness_tolerance_days: int = 0,
) -> MinimalScreenerResult:
    if price_table == PRICE_TABLE_V2:
        return _build_v2_minimal_screener_result(
            db_path=db_path,
            universe_id=universe_id,
            benchmark_ticker=benchmark_ticker,
            explicit_tickers=explicit_tickers,
            data_source=data_source,
            min_history_rows=min_history_rows,
        )

    eligibility = build_eligibility_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        explicit_tickers=explicit_tickers,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    feature_readiness = build_feature_readiness_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        benchmark_ticker=benchmark_ticker,
        explicit_tickers=explicit_tickers,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    candidate = build_candidate_type_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        benchmark_ticker=benchmark_ticker,
        explicit_tickers=explicit_tickers,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    rows = _build_table_rows(candidate)
    return MinimalScreenerResult(
        universe_id=universe_id,
        universe_source=candidate.policy.ranking.universe_source,
        benchmark_ticker=benchmark_ticker,
        price_table=price_table,
        data_source=None,
        close_input_source='close',
        benchmark_alignment_date=None,
        benchmark_lag_warning_count=0,
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


def _build_v2_minimal_screener_result(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str | None,
    explicit_tickers: Sequence[str] | None,
    data_source: str,
    min_history_rows: int,
) -> MinimalScreenerResult:
    tickers = _normalize_explicit_tickers(explicit_tickers)
    if not tickers:
        raise ValueError('V2 price_history_v2 mode requires explicit tickers for this diagnostic phase.')
    readiness = build_market_data_v2_readiness(
        db_path=db_path,
        tickers=list(tickers),
        benchmark_ticker=benchmark_ticker,
        data_source=data_source,
        min_history_rows=min_history_rows,
    )
    complete_rows = [row for row in readiness.feature_rows if row.feature_complete]
    ranked = build_baseline_ranking(
        tuple(
            BaselineRankingInput(
                ticker=row.ticker,
                rank_date=_rank_date_from_feature_row(row),
                features=row.features,
            )
            for row in complete_rows
        )
    )
    policy_rows = apply_trade_policy_diagnostics(
        tuple(
            TradePolicyInputRow(
                ticker=row.ranked_candidate.ticker,
                rank_date=row.ranked_candidate.rank_date.isoformat(),
                ranking_engine_id=row.ranked_candidate.ranking_engine_id,
                raw_rank=row.ranked_candidate.raw_rank,
                raw_score=row.ranked_candidate.raw_score,
                input_fields=row.input_fields,
            )
            for row in ranked
        )
    )
    candidate_rows = apply_candidate_type_diagnostics(
        tuple(
            CandidateTypeInputRow(
                ticker=row.ticker,
                rank_date=row.rank_date,
                ranking_engine_id=row.ranking_engine_id,
                policy_engine_id=row.policy_engine_id,
                raw_rank=row.raw_rank,
                raw_score=row.raw_score,
                trade_signal=row.trade_signal,
                policy_pass=row.policy_pass,
                policy_reasons=row.policy_reasons,
                policy_warnings=row.policy_warnings,
                above_sma50=row.above_sma50,
                above_sma200=row.above_sma200,
                positive_return_3m=row.positive_return_3m,
                positive_return_6m=row.positive_return_6m,
                positive_rs_3m=row.positive_rs_3m,
                positive_rs_6m=row.positive_rs_6m,
                acceptable_drawdown=row.acceptable_drawdown,
                acceptable_volatility=row.acceptable_volatility,
                acceptable_traded_value=row.acceptable_traded_value,
                moderate_stretch=row.moderate_stretch,
                severe_stretch=row.severe_stretch,
                drawdown_252=row.drawdown_252,
                volatility_63=row.volatility_63,
                average_traded_value_20=row.average_traded_value_20,
                distance_to_sma50=row.distance_to_sma50,
                distance_to_sma200=row.distance_to_sma200,
            )
            for row in policy_rows
        )
    )
    rows = _build_table_rows_from_sequences(candidate_rows, policy_rows, ranked)
    candidate_summary = summarize_candidate_types(candidate_rows)
    return MinimalScreenerResult(
        universe_id=universe_id,
        universe_source='explicit_tickers',
        benchmark_ticker=benchmark_ticker,
        price_table=PRICE_TABLE_V2,
        data_source=data_source,
        close_input_source='adjusted_close',
        benchmark_alignment_date=_common_v2_alignment_date(readiness.feature_rows),
        benchmark_lag_warning_count=readiness.benchmark_lag_warning_count,
        ranking_engine_id=BASELINE_RANKING_ENGINE_ID,
        policy_engine_id=TRADE_POLICY_ENGINE_ID,
        classification_engine_id=CANDIDATE_TYPE_ENGINE_ID,
        input_universe_count=len(tickers),
        structural_eligible_count=readiness.enough_history_count,
        structural_rejected_count=len(tickers) - readiness.enough_history_count,
        feature_complete_count=readiness.feature_complete_count,
        feature_incomplete_count=readiness.feature_incomplete_count,
        ranked_count=len(rows),
        trade_signal_counts=candidate_summary['trade_signal_counts'],
        candidate_type_counts=candidate_summary['candidate_type_counts'],
        structural_rejection_counts_by_reason={},
        feature_missing_reason_counts=readiness.missing_reason_counts,
        signal_type_matrix=_build_signal_type_matrix(candidate_rows),
        rows=rows,
    )


def _build_table_rows(candidate) -> tuple[MinimalScreenerTableRow, ...]:
    return _build_table_rows_from_sequences(candidate.rows, candidate.policy.rows, candidate.policy.ranking.rows)


def _build_table_rows_from_sequences(candidate_rows, policy_rows, ranking_rows) -> tuple[MinimalScreenerTableRow, ...]:
    rows: list[MinimalScreenerTableRow] = []
    for candidate_row, policy_row, ranking_row in zip(candidate_rows, policy_rows, ranking_rows, strict=True):
        ranking_ticker = ranking_row.ticker if hasattr(ranking_row, 'ticker') else ranking_row.ranked_candidate.ticker
        ranking_raw_rank = ranking_row.raw_rank if hasattr(ranking_row, 'raw_rank') else ranking_row.ranked_candidate.raw_rank
        ranking_input_fields = ranking_row.input_fields
        if (
            candidate_row.ticker != policy_row.ticker or
            candidate_row.ticker != ranking_ticker or
            candidate_row.raw_rank != policy_row.raw_rank or
            candidate_row.raw_rank != ranking_raw_rank
        ):
            raise ValueError('Candidate-type rows, policy rows, and ranking rows are not aligned.')
        features = ranking_input_fields
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
    data_source: str = 'yahoo',
) -> SelectedTickerChartDetail:
    database = ReadOnlySQLite(db_path)
    tickers = [ticker]
    if benchmark_ticker:
        tickers.append(benchmark_ticker)
    if price_table == PRICE_TABLE_V2:
        loaded = load_price_history_v2_for_tickers(db_path=str(db_path), tickers=tickers, data_source=data_source, database=database)
        loaded_rows_by_ticker = loaded.as_feature_rows_by_ticker()
    else:
        loaded = load_price_history_for_tickers(database=database, tickers=tickers, price_table=price_table)
        loaded_rows_by_ticker = loaded.rows_by_ticker
    ticker_rows = list(loaded_rows_by_ticker.get(ticker.upper(), ()))
    if not ticker_rows:
        raise ValueError(f'Mangler prisdata for valgt ticker: {ticker}')
    limited_rows = ticker_rows[-lookback_rows:]
    benchmark_rows = list(loaded_rows_by_ticker.get(benchmark_ticker.upper(), ())) if benchmark_ticker else []
    benchmark_by_date = {row.price_date.isoformat(): row.close for row in benchmark_rows}

    if benchmark_ticker and not benchmark_rows:
        return SelectedTickerChartDetail(
            ticker=ticker.upper(),
            benchmark_ticker=benchmark_ticker,
            lookback_rows=lookback_rows,
            price_points=_build_price_points(limited_rows, benchmark_by_date={}),
            loaded_tickers=tuple(sorted(loaded_rows_by_ticker)),
            warning=f'Mangler benchmark-data for {benchmark_ticker}. Viser kun prisserien for valgt ticker.',
        )

    aligned_dates = [row.price_date.isoformat() for row in limited_rows if not benchmark_ticker or row.price_date.isoformat() in benchmark_by_date]
    if benchmark_ticker and not aligned_dates:
        return SelectedTickerChartDetail(
            ticker=ticker.upper(),
            benchmark_ticker=benchmark_ticker,
            lookback_rows=lookback_rows,
            price_points=_build_price_points(limited_rows, benchmark_by_date={}),
            loaded_tickers=tuple(sorted(loaded_rows_by_ticker)),
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
        loaded_tickers=tuple(sorted(loaded_rows_by_ticker)),
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


def _normalize_explicit_tickers(tickers: Sequence[str] | None) -> tuple[str, ...]:
    if tickers is None:
        return ()
    return tuple(dict.fromkeys(str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()))


def _rank_date_from_feature_row(row) -> object:
    from datetime import date

    if isinstance(row.feature_date, date):
        return row.feature_date
    return date.fromisoformat(str(row.feature_date))


def _common_v2_alignment_date(feature_rows: Sequence) -> str | None:
    dates = [row.common_aligned_max_date for row in feature_rows if getattr(row, 'common_aligned_max_date', None)]
    return min(dates) if dates else None


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

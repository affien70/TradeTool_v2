from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from calendar import monthrange
import math
from datetime import date
from pathlib import Path

from plotly import graph_objects as go
from plotly.subplots import make_subplots

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
CHART_PERIOD_CALENDAR_MONTHS = {
    '3 mnd': 3,
    '6 mnd': 6,
    '1 år': 12,
    '2 år': 24,
    '5 år': 60,
    'Maks': None,
}
DEFAULT_CHART_PERIOD_LABEL = '1 år'


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
    ticker: str
    price_date: str
    close: float
    volume: float | None
    sma50: float | None
    sma200: float | None
    benchmark_close: float | None
    indexed_close: float | None
    indexed_benchmark: float | None
    relative_strength_line: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'price_date': self.price_date,
            'close': self.close,
            'volume': self.volume,
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
    chart_period_label: str = DEFAULT_CHART_PERIOD_LABEL
    requested_start_date: str | None = None
    requested_end_date: str | None = None
    ticker_rows_found: int = 0
    benchmark_rows_found: int = 0
    visible_rows: int = 0
    first_normalized_date: str | None = None
    first_indexed_ticker_value: float | None = None
    first_indexed_benchmark_value: float | None = None
    first_rs_index_value: float | None = None
    sma50_non_null_count: int = 0
    sma200_non_null_count: int = 0
    warning: str | None = None
    calendar_start_date: str | None = None
    first_close: float | None = None
    last_close: float | None = None
    period_return_pct: float | None = None
    close_source: str = 'close'
    baseline_date: str | None = None
    baseline_ticker_close: float | None = None
    baseline_benchmark_close: float | None = None


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


def arrow_safe_display_rows(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        {
            **row,
            'verdi': '' if row.get('verdi') is None else str(row.get('verdi')),
        }
        for row in rows
    ]


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
    max_price_date: date | None = None,
    chart_period_label: str = DEFAULT_CHART_PERIOD_LABEL,
) -> SelectedTickerChartDetail:
    database = ReadOnlySQLite(db_path)
    tickers = [ticker]
    if benchmark_ticker:
        tickers.append(benchmark_ticker)
    if price_table == PRICE_TABLE_V2:
        loaded = load_price_history_v2_for_tickers(
            db_path=str(db_path),
            tickers=tickers,
            data_source=data_source,
            max_price_date=max_price_date,
            database=database,
        )
        loaded_rows_by_ticker = loaded.as_feature_rows_by_ticker()
    else:
        loaded = load_price_history_for_tickers(database=database, tickers=tickers, price_table=price_table)
        loaded_rows_by_ticker = loaded.rows_by_ticker
        if max_price_date is not None:
            loaded_rows_by_ticker = {
                loaded_ticker: tuple(row for row in rows if row.price_date <= max_price_date)
                for loaded_ticker, rows in loaded_rows_by_ticker.items()
            }
    ticker_rows = list(loaded_rows_by_ticker.get(ticker.upper(), ()))
    if not ticker_rows:
        raise ValueError(
            'Mangler prisdata for valgt ticker: '
            f'{ticker}. Benchmark: {benchmark_ticker or "ingen"}. '
            f'Dato til og med: {max_price_date.isoformat() if max_price_date else "siste tilgjengelige"}. '
            'Rader funnet for ticker: 0.'
        )
    chart_end_date = max_price_date or ticker_rows[-1].price_date
    calendar_start = _chart_calendar_start(chart_period_label, chart_end_date)
    limited_rows = (
        [row for row in ticker_rows if row.price_date >= calendar_start]
        if calendar_start is not None else ticker_rows
    )
    benchmark_rows = list(loaded_rows_by_ticker.get(benchmark_ticker.upper(), ())) if benchmark_ticker else []
    requested_start_date = limited_rows[0].price_date.isoformat() if limited_rows else None
    requested_end_date = limited_rows[-1].price_date.isoformat() if limited_rows else None
    benchmark_by_date = {row.price_date.isoformat(): row.close for row in benchmark_rows}
    sma_lookup = _build_sma_lookup(ticker_rows)
    chart_metadata = {
        'calendar_start_date': calendar_start.isoformat() if calendar_start else None,
        'close_source': 'adjusted_close' if price_table == PRICE_TABLE_V2 else 'close',
    }

    if not limited_rows:
        return SelectedTickerChartDetail(
            ticker=ticker.upper(), benchmark_ticker=benchmark_ticker, lookback_rows=lookback_rows,
            price_points=(), loaded_tickers=tuple(sorted(loaded_rows_by_ticker)),
            chart_period_label=chart_period_label, requested_end_date=chart_end_date.isoformat(),
            ticker_rows_found=len(ticker_rows), benchmark_rows_found=len(benchmark_rows),
            warning=f'Ingen prisdata for {ticker.upper()} i valgt grafperiode.', **chart_metadata,
        )

    if benchmark_ticker and not benchmark_rows:
        return SelectedTickerChartDetail(
            ticker=ticker.upper(),
            benchmark_ticker=benchmark_ticker,
            lookback_rows=lookback_rows,
            price_points=_build_price_points(ticker=ticker.upper(), rows=limited_rows, benchmark_by_date={}, sma_lookup=sma_lookup),
            loaded_tickers=tuple(sorted(loaded_rows_by_ticker)),
            chart_period_label=chart_period_label,
            requested_start_date=requested_start_date,
            requested_end_date=requested_end_date,
            ticker_rows_found=len(ticker_rows),
            benchmark_rows_found=0,
            visible_rows=len(limited_rows),
            warning=(
                f'Mangler benchmark-data for {benchmark_ticker}. Viser kun prisserien for {ticker.upper()}. '
                f'Forespurt periode: {requested_start_date or "ukjent"} til {requested_end_date or "ukjent"}. '
                f'Rader funnet for ticker: {len(ticker_rows)}. Rader funnet for benchmark: 0.'
            ),
            first_close=float(limited_rows[0].close),
            last_close=float(limited_rows[-1].close),
            period_return_pct=_chart_return_pct(limited_rows),
            **chart_metadata,
        )

    aligned_dates = [row.price_date.isoformat() for row in limited_rows if not benchmark_ticker or row.price_date.isoformat() in benchmark_by_date]
    if benchmark_ticker and not aligned_dates:
        return SelectedTickerChartDetail(
            ticker=ticker.upper(),
            benchmark_ticker=benchmark_ticker,
            lookback_rows=lookback_rows,
            price_points=_build_price_points(ticker=ticker.upper(), rows=limited_rows, benchmark_by_date={}, sma_lookup=sma_lookup),
            loaded_tickers=tuple(sorted(loaded_rows_by_ticker)),
            chart_period_label=chart_period_label,
            requested_start_date=requested_start_date,
            requested_end_date=requested_end_date,
            ticker_rows_found=len(ticker_rows),
            benchmark_rows_found=len(benchmark_rows),
            visible_rows=len(limited_rows),
            warning=(
                f'Kunne ikke justere datoer mellom {ticker.upper()} og {benchmark_ticker}. '
                f'Forespurt periode: {requested_start_date or "ukjent"} til {requested_end_date or "ukjent"}. '
                f'Rader funnet for ticker: {len(ticker_rows)}. Rader funnet for benchmark: {len(benchmark_rows)}.'
            ),
            first_close=float(limited_rows[0].close),
            last_close=float(limited_rows[-1].close),
            period_return_pct=_chart_return_pct(limited_rows),
            **chart_metadata,
        )

    if benchmark_ticker:
        filtered_rows = [row for row in limited_rows if row.price_date.isoformat() in set(aligned_dates)]
        filtered_benchmark = {date_key: benchmark_by_date[date_key] for date_key in aligned_dates}
    else:
        filtered_rows = limited_rows
        filtered_benchmark = {}

    baseline_row = filtered_rows[0]
    if calendar_start is not None:
        # Use the prior common session even when the calendar start itself traded.
        for row in reversed(ticker_rows):
            if row.price_date < calendar_start and (not benchmark_ticker or row.price_date.isoformat() in benchmark_by_date):
                baseline_row = row
                break
    baseline_date = baseline_row.price_date.isoformat()
    ticker_baseline = float(baseline_row.close)
    benchmark_baseline = float(benchmark_by_date[baseline_date]) if benchmark_ticker else None
    chart_points = _build_price_points(
        ticker=ticker.upper(), rows=filtered_rows, benchmark_by_date=filtered_benchmark,
        sma_lookup=sma_lookup, ticker_baseline_close=ticker_baseline,
        benchmark_baseline_close=benchmark_baseline,
    )

    return SelectedTickerChartDetail(
        ticker=ticker.upper(),
        benchmark_ticker=benchmark_ticker,
        lookback_rows=lookback_rows,
        price_points=chart_points,
        loaded_tickers=tuple(sorted(loaded_rows_by_ticker)),
        chart_period_label=chart_period_label,
        requested_start_date=(filtered_rows[0].price_date.isoformat() if filtered_rows else requested_start_date),
        requested_end_date=(filtered_rows[-1].price_date.isoformat() if filtered_rows else requested_end_date),
        ticker_rows_found=len(ticker_rows),
        benchmark_rows_found=len(benchmark_rows),
        visible_rows=len(filtered_rows),
        first_normalized_date=baseline_date,
        first_indexed_ticker_value=chart_points[0].indexed_close,
        first_indexed_benchmark_value=chart_points[0].indexed_benchmark,
        first_rs_index_value=chart_points[0].relative_strength_line,
        sma50_non_null_count=sum(1 for row in filtered_rows if sma_lookup.get(row.price_date.isoformat(), (None, None))[0] is not None),
        sma200_non_null_count=sum(1 for row in filtered_rows if sma_lookup.get(row.price_date.isoformat(), (None, None))[1] is not None),
        warning=None,
        first_close=float(filtered_rows[0].close) if filtered_rows else None,
        last_close=float(filtered_rows[-1].close) if filtered_rows else None,
        period_return_pct=_chart_return_pct(filtered_rows, baseline_close=ticker_baseline),
        baseline_date=baseline_date,
        baseline_ticker_close=ticker_baseline,
        baseline_benchmark_close=benchmark_baseline,
        **chart_metadata,
    )


def build_price_chart_figure(chart_detail: SelectedTickerChartDetail) -> go.Figure | None:
    if not chart_detail.price_points:
        return None
    dates = [point.price_date for point in chart_detail.price_points]
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.8, 0.2],
    )
    figure.add_trace(
        go.Scatter(x=dates, y=[point.close for point in chart_detail.price_points], mode='lines', name='Kurs'),
        row=1,
        col=1,
    )
    if any(point.sma50 is not None for point in chart_detail.price_points):
        figure.add_trace(
            go.Scatter(x=dates, y=[point.sma50 for point in chart_detail.price_points], mode='lines', name='SMA50'),
            row=1,
            col=1,
        )
    if any(point.sma200 is not None for point in chart_detail.price_points):
        figure.add_trace(
            go.Scatter(x=dates, y=[point.sma200 for point in chart_detail.price_points], mode='lines', name='SMA200'),
            row=1,
            col=1,
        )
    figure.add_trace(
        go.Bar(x=dates, y=[point.volume for point in chart_detail.price_points], name='Volum'),
        row=2,
        col=1,
    )
    figure.update_layout(height=720, hovermode='x unified', margin={'l': 70, 'r': 30, 't': 30, 'b': 50})
    figure.update_xaxes(showticklabels=False, row=1, col=1)
    figure.update_xaxes(title_text='Dato', row=2, col=1)
    figure.update_yaxes(title_text='Pris', row=1, col=1)
    figure.update_yaxes(title_text='Volum', row=2, col=1)
    return figure


def build_relative_strength_chart_figure(chart_detail: SelectedTickerChartDetail) -> go.Figure | None:
    if not chart_detail.price_points:
        return None
    dates = [point.price_date for point in chart_detail.price_points]
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=dates,
            y=[point.indexed_close - 100.0 if point.indexed_close is not None else None for point in chart_detail.price_points],
            mode='lines',
            name=chart_detail.ticker,
        ),
    )
    if chart_detail.benchmark_ticker and any(point.indexed_benchmark is not None for point in chart_detail.price_points):
        figure.add_trace(
            go.Scatter(
                x=dates,
                y=[point.indexed_benchmark - 100.0 if point.indexed_benchmark is not None else None for point in chart_detail.price_points],
                mode='lines',
                name=chart_detail.benchmark_ticker,
            ),
        )
    figure.add_hline(y=0, line={'color': '#9ca3af', 'width': 1})
    figure.update_layout(height=720, hovermode='x unified', margin={'l': 80, 'r': 30, 't': 30, 'b': 50})
    figure.update_xaxes(title_text='Dato')
    figure.update_yaxes(title_text='Utvikling (%)')
    return figure


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


def _build_price_points(
    *,
    ticker: str,
    rows,
    benchmark_by_date: Mapping[str, float],
    sma_lookup: Mapping[str, tuple[float | None, float | None]],
    ticker_baseline_close: float | None = None,
    benchmark_baseline_close: float | None = None,
) -> tuple[ScreenerChartPoint, ...]:
    closes = [float(row.close) for row in rows]
    stock_indexed = _indexed_series(closes, base=ticker_baseline_close)
    benchmark_index_lookup: dict[str, float] = {}
    if benchmark_by_date:
        aligned_dates = [row.price_date.isoformat() for row in rows]
        values = [benchmark_by_date[date_key] for date_key in aligned_dates]
        indexed = _indexed_series(values, base=benchmark_baseline_close)
        benchmark_index_lookup = {date_key: indexed_value for date_key, indexed_value in zip(aligned_dates, indexed, strict=True)}

    points: list[ScreenerChartPoint] = []
    for index, row in enumerate(rows):
        date_key = row.price_date.isoformat()
        sma50, sma200 = sma_lookup.get(date_key, (None, None))
        indexed_close = stock_indexed[index] if index < len(stock_indexed) else None
        indexed_benchmark = benchmark_index_lookup.get(date_key)
        relative_strength_line = None
        if indexed_close is not None and indexed_benchmark not in (None, 0):
            relative_strength_line = (indexed_close / indexed_benchmark) * 100.0
        points.append(
            ScreenerChartPoint(
                ticker=ticker,
                price_date=date_key,
                close=float(row.close),
                volume=(float(row.volume) if row.volume is not None and math.isfinite(float(row.volume)) else None),
                sma50=sma50,
                sma200=sma200,
                benchmark_close=benchmark_by_date.get(date_key),
                indexed_close=indexed_close,
                indexed_benchmark=indexed_benchmark,
                relative_strength_line=relative_strength_line,
            )
        )
    return tuple(points)


def _chart_calendar_start(label: str, as_of_date: date) -> date | None:
    months = CHART_PERIOD_CALENDAR_MONTHS[label]
    if months is None:
        return None
    month_index = as_of_date.year * 12 + as_of_date.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    return date(year, month, min(as_of_date.day, monthrange(year, month)[1]))


def _chart_return_pct(rows, *, baseline_close: float | None = None) -> float | None:
    if not rows:
        return None
    base = float(rows[0].close) if baseline_close is None else baseline_close
    if not base:
        return None
    return (float(rows[-1].close) / base - 1.0) * 100.0


def _build_sma_lookup(rows) -> dict[str, tuple[float | None, float | None]]:
    closes = [float(row.close) for row in rows]
    lookup: dict[str, tuple[float | None, float | None]] = {}
    for index, row in enumerate(rows):
        lookup[row.price_date.isoformat()] = (
            _rolling_mean(closes, index, 50),
            _rolling_mean(closes, index, 200),
        )
    return lookup


def _rolling_mean(values: Sequence[float], end_index: int, window: int) -> float | None:
    start_index = end_index - window + 1
    if start_index < 0:
        return None
    window_values = values[start_index:end_index + 1]
    return sum(window_values) / len(window_values)


def _indexed_series(values: Sequence[float], *, base: float | None = None) -> list[float]:
    if not values:
        return []
    if base is None:
        base = values[0]
    if base == 0 or math.isnan(base):
        return []
    return [(value / base) * 100.0 for value in values]

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

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
    return_3m: float | int | bool | str | None
    return_6m: float | int | bool | str | None
    relative_strength_3m: float | int | bool | str | None
    relative_strength_6m: float | int | bool | str | None
    above_sma50: bool
    above_sma200: bool
    drawdown_252: float
    volatility_63: float
    average_traded_value_20: float
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
            'return_3m': self.return_3m,
            'return_6m': self.return_6m,
            'relative_strength_3m': self.relative_strength_3m,
            'relative_strength_6m': self.relative_strength_6m,
            'above_sma50': self.above_sma50,
            'above_sma200': self.above_sma200,
            'drawdown_252': self.drawdown_252,
            'volatility_63': self.volatility_63,
            'average_traded_value_20': self.average_traded_value_20,
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
                return_3m=features.get('return_3m'),
                return_6m=features.get('return_6m'),
                relative_strength_3m=features.get('relative_strength_3m'),
                relative_strength_6m=features.get('relative_strength_6m'),
                above_sma50=policy_row.above_sma50,
                above_sma200=policy_row.above_sma200,
                drawdown_252=policy_row.drawdown_252,
                volatility_63=policy_row.volatility_63,
                average_traded_value_20=policy_row.average_traded_value_20,
                policy_reasons=policy_row.policy_reasons,
                policy_warnings=policy_row.policy_warnings,
                classification_reasons=candidate_row.classification_reasons,
                classification_warnings=candidate_row.classification_warnings,
            )
        )
    return tuple(rows)


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

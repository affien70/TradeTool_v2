from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tradetool.contracts.enums import CandidateType, TradeSignal

CANDIDATE_TYPE_ENGINE_ID = 'candidate_type_v0_diagnostic'


@dataclass(frozen=True, slots=True)
class CandidateTypeInputRow:
    ticker: str
    rank_date: str
    ranking_engine_id: str
    policy_engine_id: str
    raw_rank: int
    raw_score: float
    trade_signal: TradeSignal
    policy_pass: bool
    policy_reasons: tuple[str, ...]
    policy_warnings: tuple[str, ...]
    above_sma50: bool
    above_sma200: bool
    positive_return_3m: bool
    positive_return_6m: bool
    positive_rs_3m: bool
    positive_rs_6m: bool
    acceptable_drawdown: bool
    acceptable_volatility: bool
    acceptable_traded_value: bool
    moderate_stretch: bool
    severe_stretch: bool
    drawdown_252: float
    volatility_63: float
    average_traded_value_20: float
    distance_to_sma50: float
    distance_to_sma200: float


@dataclass(frozen=True, slots=True)
class CandidateTypeDiagnosticsRow:
    ticker: str
    rank_date: str
    ranking_engine_id: str
    policy_engine_id: str
    classification_engine_id: str
    raw_rank: int
    raw_score: float
    trade_signal: TradeSignal
    candidate_type: CandidateType
    classification_reasons: tuple[str, ...]
    classification_warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'rank_date': self.rank_date,
            'ranking_engine_id': self.ranking_engine_id,
            'policy_engine_id': self.policy_engine_id,
            'classification_engine_id': self.classification_engine_id,
            'raw_rank': self.raw_rank,
            'raw_score': self.raw_score,
            'trade_signal': self.trade_signal.value,
            'candidate_type': self.candidate_type.value,
            'classification_reasons': list(self.classification_reasons),
            'classification_warnings': list(self.classification_warnings),
        }


def apply_candidate_type_diagnostics(rows: Sequence[CandidateTypeInputRow]) -> tuple[CandidateTypeDiagnosticsRow, ...]:
    return tuple(_classify_row(row) for row in rows)


def summarize_candidate_types(rows: Sequence[CandidateTypeDiagnosticsRow]) -> Mapping[str, object]:
    candidate_type_counts = Counter(row.candidate_type.value for row in rows)
    trade_signal_counts = Counter(row.trade_signal.value for row in rows)
    return {
        'row_count': len(rows),
        'candidate_type_counts': dict(sorted(candidate_type_counts.items())),
        'trade_signal_counts': dict(sorted(trade_signal_counts.items())),
    }


def _classify_row(row: CandidateTypeInputRow) -> CandidateTypeDiagnosticsRow:
    reasons: list[str] = []
    warnings: list[str] = list(row.policy_warnings)

    strong_trend = all(
        (
            row.above_sma50,
            row.above_sma200,
            row.positive_return_3m,
            row.positive_return_6m,
            row.positive_rs_3m,
            row.positive_rs_6m,
        )
    )
    quality_ok = all(
        (
            row.acceptable_drawdown,
            row.acceptable_volatility,
            row.acceptable_traded_value,
        )
    )

    severe_reject = (
        row.trade_signal == TradeSignal.AVOID and (
            not row.above_sma200 or
            (not row.positive_return_3m and not row.positive_return_6m) or
            (not row.positive_rs_3m and not row.positive_rs_6m) or
            row.severe_stretch or
            not row.acceptable_traded_value or
            not row.acceptable_volatility
        )
    )
    rebound_case = (
        row.trade_signal in {TradeSignal.REVIEW, TradeSignal.AVOID} and
        (
            not row.acceptable_drawdown or
            not row.above_sma50 or
            not row.positive_return_3m or
            not row.positive_rs_3m
        )
    )
    early_breakout = (
        row.above_sma200 and
        (row.positive_return_3m or row.positive_rs_3m) and
        (not row.positive_return_6m or not row.positive_rs_6m or not row.above_sma50)
    )
    extended_runner = (
        strong_trend and
        quality_ok and
        (not row.moderate_stretch or row.severe_stretch or bool(row.policy_warnings))
    )
    stable_leader = (
        strong_trend and
        quality_ok and
        row.moderate_stretch and
        row.trade_signal in {TradeSignal.BUY, TradeSignal.WATCH}
    )

    if stable_leader:
        candidate_type = CandidateType.STABLE_LEADER
        reasons = ['strong_trend_supported_profile']
    elif extended_runner:
        candidate_type = CandidateType.EXTENDED_RUNNER
        reasons = ['strong_trend_with_stretch_or_risk_warning']
    elif severe_reject:
        candidate_type = CandidateType.REJECT
        reasons = list(row.policy_reasons) or ['severe_practical_reject']
    elif rebound_case:
        candidate_type = CandidateType.REBOUND_CASE
        reasons = list(row.policy_reasons) or ['mixed_or_rebound_like_profile']
    elif early_breakout:
        candidate_type = CandidateType.EARLY_BREAKOUT
        reasons = ['improving_short_term_breakout_profile']
    else:
        candidate_type = CandidateType.REBOUND_CASE if row.trade_signal in {TradeSignal.REVIEW, TradeSignal.AVOID} else CandidateType.EARLY_BREAKOUT
        reasons = list(row.policy_reasons) or ['mixed_diagnostic_profile']

    return CandidateTypeDiagnosticsRow(
        ticker=row.ticker,
        rank_date=row.rank_date,
        ranking_engine_id=row.ranking_engine_id,
        policy_engine_id=row.policy_engine_id,
        classification_engine_id=CANDIDATE_TYPE_ENGINE_ID,
        raw_rank=row.raw_rank,
        raw_score=row.raw_score,
        trade_signal=row.trade_signal,
        candidate_type=candidate_type,
        classification_reasons=tuple(reasons),
        classification_warnings=tuple(warnings),
    )

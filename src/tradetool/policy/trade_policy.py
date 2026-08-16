from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tradetool.contracts.enums import TradeSignal

TRADE_POLICY_ENGINE_ID = 'trade_policy_v0_baseline_diagnostic'
MIN_ACCEPTABLE_DRAWDOWN = -0.25
MAX_ACCEPTABLE_VOLATILITY = 0.03
MAX_MODERATE_VOLATILITY = 0.05
MIN_ACCEPTABLE_TRADED_VALUE = 1_000_000.0
MIN_MODERATE_TRADED_VALUE = 250_000.0
MAX_BUY_DISTANCE_TO_SMA50 = 0.15
MAX_WATCH_DISTANCE_TO_SMA50 = 0.30
MAX_BUY_DISTANCE_TO_SMA200 = 0.35
MAX_WATCH_DISTANCE_TO_SMA200 = 0.60
MAX_BUY_RAW_RANK = 20
MAX_WATCH_RAW_RANK = 60


@dataclass(frozen=True, slots=True)
class TradePolicyInputRow:
    ticker: str
    rank_date: str
    ranking_engine_id: str
    raw_rank: int
    raw_score: float
    input_fields: Mapping[str, float | int | bool | str | None]


@dataclass(frozen=True, slots=True)
class TradePolicyDiagnosticsRow:
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

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'rank_date': self.rank_date,
            'ranking_engine_id': self.ranking_engine_id,
            'policy_engine_id': self.policy_engine_id,
            'raw_rank': self.raw_rank,
            'raw_score': self.raw_score,
            'trade_signal': self.trade_signal.value,
            'policy_pass': self.policy_pass,
            'policy_reasons': list(self.policy_reasons),
            'policy_warnings': list(self.policy_warnings),
            'above_sma50': self.above_sma50,
            'above_sma200': self.above_sma200,
            'positive_return_3m': self.positive_return_3m,
            'positive_return_6m': self.positive_return_6m,
            'positive_rs_3m': self.positive_rs_3m,
            'positive_rs_6m': self.positive_rs_6m,
            'acceptable_drawdown': self.acceptable_drawdown,
            'acceptable_volatility': self.acceptable_volatility,
            'acceptable_traded_value': self.acceptable_traded_value,
            'moderate_stretch': self.moderate_stretch,
            'severe_stretch': self.severe_stretch,
            'drawdown_252': self.drawdown_252,
            'volatility_63': self.volatility_63,
            'average_traded_value_20': self.average_traded_value_20,
            'distance_to_sma50': self.distance_to_sma50,
            'distance_to_sma200': self.distance_to_sma200,
        }


def apply_trade_policy_diagnostics(rows: Sequence[TradePolicyInputRow]) -> tuple[TradePolicyDiagnosticsRow, ...]:
    return tuple(_classify_row(row) for row in rows)


def summarize_trade_policy(rows: Sequence[TradePolicyDiagnosticsRow]) -> Mapping[str, object]:
    signal_counts = Counter(row.trade_signal.value for row in rows)
    return {
        'policy_row_count': len(rows),
        'signal_counts': dict(sorted(signal_counts.items())),
        'policy_pass_count': sum(1 for row in rows if row.policy_pass),
    }


def _classify_row(row: TradePolicyInputRow) -> TradePolicyDiagnosticsRow:
    fields = row.input_fields
    above_sma50 = bool(fields.get('above_sma50'))
    above_sma200 = bool(fields.get('above_sma200'))
    positive_return_3m = _as_float(fields.get('return_3m')) > 0.0
    positive_return_6m = _as_float(fields.get('return_6m')) > 0.0
    positive_rs_3m = _as_float(fields.get('relative_strength_3m')) > 0.0
    positive_rs_6m = _as_float(fields.get('relative_strength_6m')) > 0.0
    drawdown_252 = _as_float(fields.get('drawdown_252'))
    volatility_63 = _as_float(fields.get('volatility_63'))
    average_traded_value_20 = _as_float(fields.get('average_traded_value_20'))
    distance_to_sma50 = _as_float(fields.get('distance_to_sma50'))
    distance_to_sma200 = _as_float(fields.get('distance_to_sma200'))

    acceptable_drawdown = drawdown_252 >= MIN_ACCEPTABLE_DRAWDOWN
    acceptable_volatility = volatility_63 <= MAX_ACCEPTABLE_VOLATILITY
    acceptable_traded_value = average_traded_value_20 >= MIN_ACCEPTABLE_TRADED_VALUE
    moderate_stretch = (
        abs(distance_to_sma50) <= MAX_BUY_DISTANCE_TO_SMA50 and
        0.0 <= distance_to_sma200 <= MAX_BUY_DISTANCE_TO_SMA200
    )
    severe_stretch = (
        abs(distance_to_sma50) > MAX_WATCH_DISTANCE_TO_SMA50 or
        distance_to_sma200 > MAX_WATCH_DISTANCE_TO_SMA200
    )

    reasons: list[str] = []
    warnings: list[str] = []

    if not above_sma200:
        reasons.append('below_sma200')
    if not above_sma50:
        reasons.append('below_sma50')
    if not positive_return_3m:
        reasons.append('non_positive_return_3m')
    if not positive_return_6m:
        reasons.append('non_positive_return_6m')
    if not positive_rs_3m:
        reasons.append('non_positive_relative_strength_3m')
    if not positive_rs_6m:
        reasons.append('non_positive_relative_strength_6m')
    if not acceptable_drawdown:
        reasons.append('deep_drawdown')
    if average_traded_value_20 < MIN_MODERATE_TRADED_VALUE:
        reasons.append('very_low_traded_value')
    elif not acceptable_traded_value:
        warnings.append('moderate_traded_value')
    if volatility_63 > MAX_MODERATE_VOLATILITY:
        reasons.append('high_volatility')
    elif not acceptable_volatility:
        warnings.append('moderate_volatility')
    if severe_stretch:
        reasons.append('extreme_stretch')
    elif not moderate_stretch:
        warnings.append('moderate_stretch')
    if row.raw_rank > MAX_WATCH_RAW_RANK:
        warnings.append('lower_ranked_candidate')

    strong_core = all(
        (
            above_sma200,
            above_sma50,
            positive_return_3m,
            positive_return_6m,
            positive_rs_3m,
            positive_rs_6m,
            acceptable_drawdown,
            acceptable_volatility,
            acceptable_traded_value,
            moderate_stretch,
            row.raw_rank <= MAX_BUY_RAW_RANK,
        )
    )
    watch_core = all(
        (
            above_sma200,
            above_sma50,
            positive_rs_3m,
            positive_rs_6m,
            positive_return_3m,
            positive_return_6m,
            drawdown_252 >= -0.40,
            volatility_63 <= MAX_MODERATE_VOLATILITY,
            average_traded_value_20 >= MIN_MODERATE_TRADED_VALUE,
            not severe_stretch,
            row.raw_rank <= MAX_WATCH_RAW_RANK,
        )
    )
    severe_failure = any(
        (
            not above_sma200 and not above_sma50,
            drawdown_252 < -0.40,
            volatility_63 > 0.08,
            average_traded_value_20 < MIN_MODERATE_TRADED_VALUE,
            severe_stretch,
            (not positive_rs_3m and not positive_rs_6m),
            (not positive_return_3m and not positive_return_6m),
        )
    )

    if strong_core:
        trade_signal = TradeSignal.BUY
        policy_pass = True
        reasons = ['strong_trend_profile']
    elif watch_core:
        trade_signal = TradeSignal.WATCH
        policy_pass = True
        if not reasons:
            reasons = ['watchlist_candidate']
    elif severe_failure:
        trade_signal = TradeSignal.AVOID
        policy_pass = False
        if not reasons:
            reasons = ['severe_practical_risk']
    else:
        trade_signal = TradeSignal.REVIEW
        policy_pass = False
        if not reasons:
            reasons = ['mixed_practical_profile']

    return TradePolicyDiagnosticsRow(
        ticker=row.ticker,
        rank_date=row.rank_date,
        ranking_engine_id=row.ranking_engine_id,
        policy_engine_id=TRADE_POLICY_ENGINE_ID,
        raw_rank=row.raw_rank,
        raw_score=row.raw_score,
        trade_signal=trade_signal,
        policy_pass=policy_pass,
        policy_reasons=tuple(reasons),
        policy_warnings=tuple(warnings),
        above_sma50=above_sma50,
        above_sma200=above_sma200,
        positive_return_3m=positive_return_3m,
        positive_return_6m=positive_return_6m,
        positive_rs_3m=positive_rs_3m,
        positive_rs_6m=positive_rs_6m,
        acceptable_drawdown=acceptable_drawdown,
        acceptable_volatility=acceptable_volatility,
        acceptable_traded_value=acceptable_traded_value,
        moderate_stretch=moderate_stretch,
        severe_stretch=severe_stretch,
        drawdown_252=drawdown_252,
        volatility_63=volatility_63,
        average_traded_value_20=average_traded_value_20,
        distance_to_sma50=distance_to_sma50,
        distance_to_sma200=distance_to_sma200,
    )


def _as_float(value: float | int | bool | str | None) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0

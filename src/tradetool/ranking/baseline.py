from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from tradetool.contracts.models import RankedCandidate

BASELINE_RANKING_ENGINE_ID = 'baseline_v0_price_volume_rs'


@dataclass(frozen=True, slots=True)
class BaselineRankingInput:
    ticker: str
    rank_date: date
    features: Mapping[str, float | int | bool | str | None]


@dataclass(frozen=True, slots=True)
class BaselineRankingResult:
    ranked_candidate: RankedCandidate
    contributions: Mapping[str, float]
    component_flags: Mapping[str, bool]
    input_fields: Mapping[str, float | int | bool | str | None]


def build_baseline_ranking(inputs: Sequence[BaselineRankingInput]) -> tuple[BaselineRankingResult, ...]:
    scored: list[tuple[str, float, float, float, Mapping[str, float], Mapping[str, bool], Mapping[str, float | int | bool | str | None], date]] = []
    for item in inputs:
        contributions, flags = _score_components(item.features)
        raw_score = sum(contributions.values())
        rs6m = _as_float(item.features.get('relative_strength_6m'))
        rs3m = _as_float(item.features.get('relative_strength_3m'))
        scored.append((item.ticker, raw_score, rs6m, rs3m, contributions, flags, item.features, item.rank_date))

    scored.sort(key=lambda row: (-row[1], -row[2], -row[3], row[0]))

    results: list[BaselineRankingResult] = []
    for index, (ticker, raw_score, _rs6m, _rs3m, contributions, flags, features, rank_date) in enumerate(scored, start=1):
        results.append(
            BaselineRankingResult(
                ranked_candidate=RankedCandidate(
                    ticker=ticker,
                    rank_date=rank_date,
                    ranking_engine_id=BASELINE_RANKING_ENGINE_ID,
                    raw_rank=index,
                    raw_score=raw_score,
                ),
                contributions=contributions,
                component_flags=flags,
                input_fields=features,
            )
        )
    return tuple(results)


def _score_components(features: Mapping[str, float | int | bool | str | None]) -> tuple[dict[str, float], dict[str, bool]]:
    contributions = {
        'positive_rs_3m': 2.0 if _as_float(features.get('relative_strength_3m')) > 0.0 else 0.0,
        'positive_rs_6m': 2.5 if _as_float(features.get('relative_strength_6m')) > 0.0 else 0.0,
        'positive_return_3m': 1.0 if _as_float(features.get('return_3m')) > 0.0 else 0.0,
        'positive_return_6m': 1.5 if _as_float(features.get('return_6m')) > 0.0 else 0.0,
        'above_sma200': 1.5 if bool(features.get('above_sma200')) else 0.0,
        'above_sma50': 0.5 if bool(features.get('above_sma50')) else 0.0,
        'controlled_drawdown': _drawdown_score(_as_float(features.get('drawdown_252'))),
        'controlled_volatility': _volatility_score(_as_float(features.get('volatility_63'))),
        'sufficient_traded_value': _traded_value_score(_as_float(features.get('average_traded_value_20'))),
        'moderate_stretch': _stretch_score(
            _as_float(features.get('distance_to_sma50')),
            _as_float(features.get('distance_to_sma200')),
        ),
    }
    flags = {key: value > 0 for key, value in contributions.items()}
    return contributions, flags


def _drawdown_score(drawdown: float) -> float:
    if drawdown >= -0.25:
        return 1.0
    if drawdown >= -0.40:
        return 0.25
    return -1.0


def _volatility_score(volatility: float) -> float:
    if volatility <= 0.03:
        return 0.75
    if volatility <= 0.05:
        return 0.25
    return -0.75


def _traded_value_score(traded_value: float) -> float:
    if traded_value >= 1_000_000.0:
        return 1.0
    if traded_value >= 250_000.0:
        return 0.5
    return -1.0


def _stretch_score(distance_to_sma50: float, distance_to_sma200: float) -> float:
    score = 0.0
    if abs(distance_to_sma50) <= 0.15:
        score += 0.5
    elif abs(distance_to_sma50) > 0.30:
        score -= 0.5
    if 0.0 <= distance_to_sma200 <= 0.35:
        score += 0.5
    elif distance_to_sma200 > 0.60:
        score -= 0.5
    return score


def _as_float(value: float | int | bool | str | None) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0

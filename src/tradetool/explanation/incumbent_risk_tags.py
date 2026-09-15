from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

LOW_LIQUIDITY_THRESHOLD = 750_000.0
VERY_LOW_LIQUIDITY_THRESHOLD = 250_000.0
HIGH_VOLATILITY_THRESHOLD = 0.05
DEEP_DRAWDOWN_THRESHOLD = -0.40
EXTREME_SMA200_STRETCH_THRESHOLD = 0.60

RISK_LEVEL_LOW = 'LOW'
RISK_LEVEL_MEDIUM = 'MEDIUM'
RISK_LEVEL_HIGH = 'HIGH'

MATERIAL_HIGH_RISK_TAGS = frozenset(
    {
        'very_low_liquidity',
        'high_volatility',
        'deep_drawdown',
        'below_sma200',
        'extreme_sma200_stretch',
    }
)


@dataclass(frozen=True, slots=True)
class IncumbentRiskTagResult:
    risk_level: str
    risk_tags: tuple[str, ...]
    risk_explanation_no: str


def build_incumbent_risk_tags(fields: Mapping[str, object]) -> IncumbentRiskTagResult:
    tags: list[str] = []
    missing: list[str] = []

    traded_value = _optional_float(fields.get('average_traded_value_20'))
    if traded_value is None:
        missing.append('likviditet')
    elif traded_value < VERY_LOW_LIQUIDITY_THRESHOLD:
        tags.append('very_low_liquidity')
    elif traded_value < LOW_LIQUIDITY_THRESHOLD:
        tags.append('low_liquidity')

    volatility = _optional_float(fields.get('volatility_63'))
    if volatility is None:
        missing.append('volatilitet')
    elif volatility > HIGH_VOLATILITY_THRESHOLD:
        tags.append('high_volatility')

    drawdown = _optional_float(fields.get('drawdown_252'))
    if drawdown is None:
        missing.append('drawdown')
    elif drawdown < DEEP_DRAWDOWN_THRESHOLD:
        tags.append('deep_drawdown')

    above_sma200 = fields.get('above_sma200')
    if above_sma200 is None:
        missing.append('SMA200-status')
    elif not bool(above_sma200):
        tags.append('below_sma200')

    return_3m = _optional_float(fields.get('return_3m'))
    if return_3m is None:
        missing.append('3m avkastning')
    elif return_3m < 0.0:
        tags.append('negative_3m_return')

    rs_3m = _optional_float(fields.get('relative_strength_3m'))
    if rs_3m is None:
        missing.append('3m relativ styrke')
    elif rs_3m < 0.0:
        tags.append('negative_3m_rs')

    distance_to_sma200 = _optional_float(fields.get('distance_to_sma200'))
    if distance_to_sma200 is None:
        missing.append('SMA200-avstand')
    elif abs(distance_to_sma200) > EXTREME_SMA200_STRETCH_THRESHOLD:
        tags.append('extreme_sma200_stretch')

    if missing:
        tags.append('missing_risk_metric')

    deduped_tags = tuple(dict.fromkeys(tags))
    risk_level = _risk_level(deduped_tags)
    return IncumbentRiskTagResult(
        risk_level=risk_level,
        risk_tags=deduped_tags,
        risk_explanation_no=_explanation(risk_level=risk_level, tags=deduped_tags, missing=tuple(dict.fromkeys(missing))),
    )


def _risk_level(tags: tuple[str, ...]) -> str:
    material_high_count = sum(1 for tag in tags if tag in MATERIAL_HIGH_RISK_TAGS)
    if material_high_count >= 1 or 'very_low_liquidity' in tags:
        return RISK_LEVEL_HIGH
    if tags:
        return RISK_LEVEL_MEDIUM
    return RISK_LEVEL_LOW


def _explanation(*, risk_level: str, tags: tuple[str, ...], missing: tuple[str, ...]) -> str:
    if not tags:
        return 'Lav risiko i denne diagnostikken: ingen tydelige likviditets-, trend- eller risikoflagg.'
    parts = [_TAG_TEXT[tag] for tag in tags if tag in _TAG_TEXT]
    if missing:
        parts.append('Mangler risikomålinger: ' + ', '.join(missing) + '.')
    prefix = {
        RISK_LEVEL_LOW: 'Lav risiko.',
        RISK_LEVEL_MEDIUM: 'Middels risiko.',
        RISK_LEVEL_HIGH: 'Høy risiko.',
    }[risk_level]
    return prefix + ' ' + ' '.join(parts)


def _optional_float(value: object) -> float | None:
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        parsed = 1.0 if value else 0.0
    elif isinstance(value, (int, float)):
        parsed = float(value)
    else:
        parsed = float(str(value))
    if not math.isfinite(parsed):
        return None
    return parsed


_TAG_TEXT = {
    'low_liquidity': 'Likviditeten er lavere enn ønsket terskel.',
    'very_low_liquidity': 'Likviditeten er svært lav.',
    'high_volatility': 'Volatiliteten er høy.',
    'deep_drawdown': 'Aksjen har hatt dypt drawdown.',
    'below_sma200': 'Kursen ligger under SMA200.',
    'negative_3m_return': 'Tre måneders avkastning er negativ.',
    'negative_3m_rs': 'Tre måneders relativ styrke er negativ.',
    'extreme_sma200_stretch': 'Avstanden til SMA200 er ekstrem.',
    'missing_risk_metric': 'Noen risikomålinger mangler.',
}

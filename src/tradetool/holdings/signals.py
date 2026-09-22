"""Pure Holdings signal policy with no persistence, market-data, or UI integration."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class HoldingSignalInputs:
    below_fast_sma: bool
    weak_rs: bool
    below_cost_basis: bool
    drop_from_peak: bool
    below_long_sma: bool = False
    short_term_return: float | None = None
    momentum_acceleration: float | None = None
    fast_sma_slope_positive: bool | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ('short_term_return', self.short_term_return),
            ('momentum_acceleration', self.momentum_acceleration),
        ):
            if value is not None and not isfinite(float(value)):
                raise ValueError(f'{field_name} must be finite when provided.')


@dataclass(frozen=True, slots=True)
class HoldingSignalRules:
    sell_if_rs_weak: bool = True
    sell_if_below_cost_basis: bool = True
    sell_if_drop_from_peak: bool = False
    sell_fast_sma_days: int = 100
    atr_multiplier: float = 2.5
    sell_rs_threshold: float = 1.05

    def __post_init__(self) -> None:
        if self.sell_fast_sma_days < 0:
            raise ValueError('sell_fast_sma_days must not be negative.')
        if not isfinite(float(self.atr_multiplier)) or self.atr_multiplier <= 0:
            raise ValueError('atr_multiplier must be positive and finite.')
        if not isfinite(float(self.sell_rs_threshold)):
            raise ValueError('sell_rs_threshold must be finite.')


@dataclass(frozen=True, slots=True)
class HoldingSignalEvaluation:
    action: str
    reasons: tuple[str, ...] = ()

    @property
    def primary_reason(self) -> str | None:
        return self.reasons[0] if self.reasons else None

    @property
    def supporting_reasons(self) -> tuple[str, ...]:
        return self.reasons[1:]


def evaluate_holding_signal(
    inputs: HoldingSignalInputs,
    rules: HoldingSignalRules = HoldingSignalRules(),
) -> HoldingSignalEvaluation:
    triggered_reasons: list[str] = []
    warning_reasons: list[str] = []
    below_fast_sma = rules.sell_fast_sma_days > 0 and inputs.below_fast_sma
    weak_rs = rules.sell_if_rs_weak and inputs.weak_rs
    below_cost_basis = rules.sell_if_below_cost_basis and inputs.below_cost_basis
    drop_from_peak = rules.sell_if_drop_from_peak and inputs.drop_from_peak
    short_term_momentum_negative = inputs.short_term_return is not None and inputs.short_term_return < 0.0
    short_term_momentum_strong = inputs.short_term_return is not None and inputs.short_term_return >= 0.0
    fast_sma_rising = inputs.fast_sma_slope_positive is True
    sma_label = f'SMA{rules.sell_fast_sma_days}'
    sma_reason = f'Kurs under {sma_label}'
    atr_reason = f'ATR-stop brutt ({rules.atr_multiplier:.1f}x ATR)'
    weak_rs_reason = f'RS < {rules.sell_rs_threshold:.2f}'
    negative_momentum_reason = '1m momentum er negativt'
    falling_sma_reason = f'{sma_label}-trend er ikke stigende'

    if below_cost_basis:
        triggered_reasons.append('10% under kostpris')
    if inputs.below_long_sma:
        triggered_reasons.append('Langsiktig trend brutt: kurs under SMA200')

    sma_confirmations: list[str] = []
    if drop_from_peak:
        sma_confirmations.append('ATR-stop brutt')
    if weak_rs:
        sma_confirmations.append(weak_rs_reason)
    if short_term_momentum_negative:
        sma_confirmations.append(negative_momentum_reason)
    if inputs.fast_sma_slope_positive is False:
        sma_confirmations.append(falling_sma_reason)

    atr_confirmations: list[str] = []
    if below_fast_sma:
        atr_confirmations.append(sma_reason)
    if weak_rs:
        atr_confirmations.append(weak_rs_reason)
    if short_term_momentum_negative:
        atr_confirmations.append(negative_momentum_reason)

    if below_fast_sma:
        if sma_confirmations:
            _append_unique(triggered_reasons, (sma_reason, *sma_confirmations))
        else:
            warning_reasons.append(
                f'Kurs under {sma_label}, men RS, momentum og {sma_label}-trend er fortsatt sterke.'
                if not weak_rs and short_term_momentum_strong and fast_sma_rising
                else f'Kurs under {sma_label} uten bekreftet svakhet.'
            )

    if drop_from_peak:
        if atr_confirmations:
            _append_unique(triggered_reasons, (atr_reason, *atr_confirmations))
        else:
            warning_reasons.append(
                'ATR trailing stop brutt, men trend, RS og momentum er fortsatt sterke.'
                if not below_fast_sma and not weak_rs and short_term_momentum_strong
                else 'ATR trailing stop brutt uten bekreftet svakhet.'
            )

    if triggered_reasons:
        return HoldingSignalEvaluation(action='SELL', reasons=tuple(triggered_reasons))
    if warning_reasons:
        return HoldingSignalEvaluation(action='F\u00d8LG MED', reasons=tuple(warning_reasons))
    return HoldingSignalEvaluation(action='HOLD')


def _append_unique(target: list[str], values: tuple[str, ...]) -> None:
    for value in values:
        if value not in target:
            target.append(value)
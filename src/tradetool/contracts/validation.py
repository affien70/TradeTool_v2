from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from tradetool.contracts.enums import CostBasisStatus


def require_non_empty_text(value: str, *, field_name: str) -> str:
    normalized = str(value or '').strip()
    if not normalized:
        raise ValueError(f'{field_name} must be non-empty.')
    return normalized


def normalize_reason_sequence(reasons: Sequence[str], *, field_name: str) -> tuple[str, ...]:
    normalized = tuple(str(reason).strip() for reason in reasons if str(reason).strip())
    if not normalized:
        raise ValueError(f'{field_name} must contain at least one reason.')
    return normalized


def validate_ohlc(*, open_value: float, high_value: float, low_value: float, close_value: float) -> None:
    if low_value > high_value:
        raise ValueError('low may not exceed high.')
    for field_name, value in {
        'open': open_value,
        'close': close_value,
    }.items():
        if value < low_value or value > high_value:
            raise ValueError(f'{field_name} must be between low and high.')


def validate_positive_rank(rank: int) -> None:
    if int(rank) <= 0:
        raise ValueError('rank must be a positive one-based integer.')


def validate_coverage_counts(*, input_universe_count: int, valid_ticker_count: int, market_data_coverage_count: int, enough_history_count: int, feature_complete_count: int, eligible_count: int, ranked_count: int, failed_count: int) -> None:
    counts = [
        input_universe_count,
        valid_ticker_count,
        market_data_coverage_count,
        enough_history_count,
        feature_complete_count,
        eligible_count,
        ranked_count,
        failed_count,
    ]
    if any(value < 0 for value in counts):
        raise ValueError('coverage counts may not be negative.')
    if valid_ticker_count > input_universe_count:
        raise ValueError('valid_ticker_count cannot exceed input_universe_count.')
    if market_data_coverage_count > valid_ticker_count:
        raise ValueError('market_data_coverage_count cannot exceed valid_ticker_count.')
    if enough_history_count > market_data_coverage_count:
        raise ValueError('enough_history_count cannot exceed market_data_coverage_count.')
    if feature_complete_count > enough_history_count:
        raise ValueError('feature_complete_count cannot exceed enough_history_count.')
    if eligible_count > feature_complete_count:
        raise ValueError('eligible_count cannot exceed feature_complete_count.')
    if ranked_count > eligible_count:
        raise ValueError('ranked_count cannot exceed eligible_count.')
    if failed_count > input_universe_count:
        raise ValueError('failed_count cannot exceed input_universe_count.')


def validate_average_cost(cost_basis_status: CostBasisStatus, average_cost: float | None) -> None:
    if cost_basis_status is CostBasisStatus.KNOWN and average_cost is None:
        raise ValueError('known cost basis requires average_cost.')


def ensure_mapping(value: Mapping[str, Any], *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f'{field_name} must be a mapping.')
    return value


def ensure_date(value: date, *, field_name: str) -> date:
    if not isinstance(value, date):
        raise TypeError(f'{field_name} must be a date instance.')
    return value

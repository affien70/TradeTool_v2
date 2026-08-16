from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from tradetool.contracts.models import EligibilityResult

REASON_INVALID_OR_EMPTY_TICKER = 'invalid_or_empty_ticker'
REASON_MISSING_MARKET_DATA = 'missing_market_data'
REASON_INSUFFICIENT_HISTORY = 'insufficient_history_lt_min_rows'
REASON_STALE_LATEST_PRICE_DATE = 'stale_latest_price_date'
REASON_DUPLICATE_TICKER_DATE_ROWS = 'duplicate_ticker_date_rows'
REASON_INVALID_OHLC = 'invalid_ohlc_rows'
REASON_MISSING_REQUIRED_OHLCV_COLUMNS = 'missing_required_ohlcv_columns'


@dataclass(frozen=True, slots=True)
class StructuralEligibilityInput:
    ticker: str
    feature_date: date
    row_count: int
    latest_price_date: date | None
    universe_max_price_date: date | None
    has_market_data: bool
    has_duplicate_rows: bool
    has_invalid_ohlc: bool
    has_required_ohlcv_columns: bool
    is_ticker_valid: bool = True
    source_universe: str | None = None


def evaluate_structural_eligibility(
    eligibility_input: StructuralEligibilityInput,
    *,
    min_history_rows: int = 252,
    freshness_tolerance_days: int = 0,
) -> EligibilityResult:
    reasons: list[str] = []
    if not eligibility_input.is_ticker_valid:
        reasons.append(REASON_INVALID_OR_EMPTY_TICKER)
    if not eligibility_input.has_required_ohlcv_columns:
        reasons.append(REASON_MISSING_REQUIRED_OHLCV_COLUMNS)
    if not eligibility_input.has_market_data:
        reasons.append(REASON_MISSING_MARKET_DATA)
    if eligibility_input.row_count < min_history_rows:
        reasons.append(REASON_INSUFFICIENT_HISTORY)
    if _is_stale(
        latest_price_date=eligibility_input.latest_price_date,
        universe_max_price_date=eligibility_input.universe_max_price_date,
        freshness_tolerance_days=freshness_tolerance_days,
        has_market_data=eligibility_input.has_market_data,
    ):
        reasons.append(REASON_STALE_LATEST_PRICE_DATE)
    if eligibility_input.has_duplicate_rows:
        reasons.append(REASON_DUPLICATE_TICKER_DATE_ROWS)
    if eligibility_input.has_invalid_ohlc:
        reasons.append(REASON_INVALID_OHLC)

    return EligibilityResult(
        ticker=eligibility_input.ticker,
        feature_date=eligibility_input.feature_date,
        eligible=not reasons,
        rejection_reasons=tuple(reasons),
        coverage_notes=tuple(
            note
            for note in (
                None if eligibility_input.source_universe is None else f'source_universe={eligibility_input.source_universe}',
            )
            if note is not None
        ),
        threshold_snapshot={
            'min_history_rows': min_history_rows,
            'freshness_tolerance_days': freshness_tolerance_days,
        },
    )


def build_eligibility_results(
    eligibility_inputs: Sequence[StructuralEligibilityInput],
    *,
    min_history_rows: int = 252,
    freshness_tolerance_days: int = 0,
) -> tuple[EligibilityResult, ...]:
    return tuple(
        evaluate_structural_eligibility(
            eligibility_input,
            min_history_rows=min_history_rows,
            freshness_tolerance_days=freshness_tolerance_days,
        )
        for eligibility_input in eligibility_inputs
    )


def summarize_eligibility_results(results: Sequence[EligibilityResult]) -> Mapping[str, object]:
    rejection_counts: Counter[str] = Counter()
    eligible_count = 0
    for result in results:
        if result.eligible:
            eligible_count += 1
        else:
            rejection_counts.update(result.rejection_reasons)
    return {
        'eligible_count': eligible_count,
        'rejected_count': len(results) - eligible_count,
        'rejection_counts_by_reason': dict(sorted(rejection_counts.items())),
    }


def _is_stale(
    *,
    latest_price_date: date | None,
    universe_max_price_date: date | None,
    freshness_tolerance_days: int,
    has_market_data: bool,
) -> bool:
    if not has_market_data:
        return False
    if latest_price_date is None or universe_max_price_date is None:
        return True
    return (universe_max_price_date - latest_price_date).days > freshness_tolerance_days

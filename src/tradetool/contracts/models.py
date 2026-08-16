from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from tradetool.contracts.enums import CandidateType, CostBasisStatus, HoldingsSignal, TradeSignal
from tradetool.contracts.validation import (
    ensure_date,
    ensure_mapping,
    normalize_reason_sequence,
    require_non_empty_text,
    validate_average_cost,
    validate_coverage_counts,
    validate_ohlc,
    validate_positive_rank,
)

FeatureValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class UniverseMember:
    universe_id: str
    ticker: str
    as_of_date: date
    membership_status: str
    instrument_name: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None

    def __post_init__(self) -> None:
        require_non_empty_text(self.universe_id, field_name='universe_id')
        require_non_empty_text(self.ticker, field_name='ticker')
        ensure_date(self.as_of_date, field_name='as_of_date')
        require_non_empty_text(self.membership_status, field_name='membership_status')


@dataclass(frozen=True, slots=True)
class PriceHistoryRow:
    ticker: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    adjusted_close: float | None = None
    source_metadata: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        require_non_empty_text(self.ticker, field_name='ticker')
        ensure_date(self.date, field_name='date')
        validate_ohlc(open_value=self.open, high_value=self.high, low_value=self.low, close_value=self.close)
        if self.volume is None:
            raise ValueError('volume may not be null.')
        if self.source_metadata is not None:
            ensure_mapping(self.source_metadata, field_name='source_metadata')


@dataclass(frozen=True, slots=True)
class BenchmarkHistoryRow:
    benchmark_id: str
    date: date
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None

    def __post_init__(self) -> None:
        require_non_empty_text(self.benchmark_id, field_name='benchmark_id')
        ensure_date(self.date, field_name='date')


@dataclass(frozen=True, slots=True)
class FeatureRow:
    ticker: str
    feature_date: date
    coverage_status: str
    features: Mapping[str, FeatureValue]
    sector: str | None = None
    benchmark_context: str | None = None
    artifact_compatibility_markers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_non_empty_text(self.ticker, field_name='ticker')
        ensure_date(self.feature_date, field_name='feature_date')
        require_non_empty_text(self.coverage_status, field_name='coverage_status')
        ensure_mapping(self.features, field_name='features')


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    ticker: str
    feature_date: date
    eligible: bool
    rejection_reasons: tuple[str, ...] = ()
    liquidity_status: str | None = None
    coverage_notes: tuple[str, ...] = ()
    threshold_snapshot: Mapping[str, FeatureValue] | None = None

    def __post_init__(self) -> None:
        require_non_empty_text(self.ticker, field_name='ticker')
        ensure_date(self.feature_date, field_name='feature_date')
        if self.eligible and self.rejection_reasons:
            object.__setattr__(self, 'rejection_reasons', tuple(str(reason).strip() for reason in self.rejection_reasons if str(reason).strip()))
        elif not self.eligible:
            object.__setattr__(self, 'rejection_reasons', normalize_reason_sequence(self.rejection_reasons, field_name='rejection_reasons'))
        if self.threshold_snapshot is not None:
            ensure_mapping(self.threshold_snapshot, field_name='threshold_snapshot')


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    ticker: str
    rank_date: date
    ranking_engine_id: str
    raw_rank: int
    raw_score: float
    baseline_rank: int | None = None
    challenger_rank: int | None = None
    practical_score: float | None = None

    def __post_init__(self) -> None:
        require_non_empty_text(self.ticker, field_name='ticker')
        ensure_date(self.rank_date, field_name='rank_date')
        require_non_empty_text(self.ranking_engine_id, field_name='ranking_engine_id')
        validate_positive_rank(self.raw_rank)
        if self.baseline_rank is not None:
            validate_positive_rank(self.baseline_rank)
        if self.challenger_rank is not None:
            validate_positive_rank(self.challenger_rank)


@dataclass(frozen=True, slots=True)
class CandidateClassification:
    ticker: str
    rank_date: date
    candidate_type: CandidateType
    trade_signal: TradeSignal
    classification_reasons: tuple[str, ...]
    practical_score: float | None = None
    stretch_state: str | None = None
    risk_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_non_empty_text(self.ticker, field_name='ticker')
        ensure_date(self.rank_date, field_name='rank_date')
        object.__setattr__(self, 'classification_reasons', normalize_reason_sequence(self.classification_reasons, field_name='classification_reasons'))


@dataclass(frozen=True, slots=True)
class ExplanationResult:
    ticker: str
    rank_date: date
    summary_text: str
    reason_codes: tuple[str, ...]
    risk_bullets: tuple[str, ...] = ()
    benchmark_context: str | None = None
    warning_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_non_empty_text(self.ticker, field_name='ticker')
        ensure_date(self.rank_date, field_name='rank_date')
        require_non_empty_text(self.summary_text, field_name='summary_text')
        object.__setattr__(self, 'reason_codes', normalize_reason_sequence(self.reason_codes, field_name='reason_codes'))


@dataclass(frozen=True, slots=True)
class HoldingsPosition:
    ticker: str
    quantity: float
    position_status: str
    cost_basis_status: CostBasisStatus
    snapshot_date: date
    average_cost: float | None = None
    market_value: float | None = None
    unrealized_pl: float | None = None
    benchmark_id: str | None = None

    def __post_init__(self) -> None:
        require_non_empty_text(self.ticker, field_name='ticker')
        require_non_empty_text(self.position_status, field_name='position_status')
        ensure_date(self.snapshot_date, field_name='snapshot_date')
        validate_average_cost(self.cost_basis_status, self.average_cost)


@dataclass(frozen=True, slots=True)
class HoldingsSignalResult:
    ticker: str
    signal_date: date
    holdings_signal: HoldingsSignal
    signal_reasons: tuple[str, ...]
    warning_flags: tuple[str, ...] = ()
    benchmark_relative_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_non_empty_text(self.ticker, field_name='ticker')
        ensure_date(self.signal_date, field_name='signal_date')
        object.__setattr__(self, 'signal_reasons', normalize_reason_sequence(self.signal_reasons, field_name='signal_reasons'))


@dataclass(frozen=True, slots=True)
class MLArtifactMetadata:
    artifact_id: str
    artifact_path: str
    checksum_set: Mapping[str, str]
    training_start_date: date
    training_end_date: date
    validation_definition: str
    feature_contract_version: str
    benchmark: str | None = None
    universe_snapshot_id: str | None = None
    model_family: str | None = None
    promotion_state: str | None = None

    def __post_init__(self) -> None:
        require_non_empty_text(self.artifact_id, field_name='artifact_id')
        require_non_empty_text(self.artifact_path, field_name='artifact_path')
        ensure_mapping(self.checksum_set, field_name='checksum_set')
        ensure_date(self.training_start_date, field_name='training_start_date')
        ensure_date(self.training_end_date, field_name='training_end_date')
        require_non_empty_text(self.validation_definition, field_name='validation_definition')
        require_non_empty_text(self.feature_contract_version, field_name='feature_contract_version')


@dataclass(frozen=True, slots=True)
class CoverageReport:
    run_id: str
    run_date: date
    input_universe_count: int
    valid_ticker_count: int
    market_data_coverage_count: int
    enough_history_count: int
    feature_complete_count: int
    eligible_count: int
    ranked_count: int
    failed_count: int
    rejection_counts_by_reason: Mapping[str, int]
    latest_data_date_distribution: Mapping[str, int]
    benchmark_coverage_notes: tuple[str, ...] = ()
    missing_ticker_samples: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_non_empty_text(self.run_id, field_name='run_id')
        ensure_date(self.run_date, field_name='run_date')
        ensure_mapping(self.rejection_counts_by_reason, field_name='rejection_counts_by_reason')
        ensure_mapping(self.latest_data_date_distribution, field_name='latest_data_date_distribution')
        validate_coverage_counts(
            input_universe_count=self.input_universe_count,
            valid_ticker_count=self.valid_ticker_count,
            market_data_coverage_count=self.market_data_coverage_count,
            enough_history_count=self.enough_history_count,
            feature_complete_count=self.feature_complete_count,
            eligible_count=self.eligible_count,
            ranked_count=self.ranked_count,
            failed_count=self.failed_count,
        )

    @property
    def normalized_valid_ticker_count(self) -> int:
        return self.valid_ticker_count


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    comparison_id: str
    baseline_id: str
    challenger_id: str
    universe_id: str
    run_date: date
    cost_assumption_id: str
    coverage_summary: Mapping[str, Any]
    metric_summary: Mapping[str, Any]
    recommendation: str
    focus_ticker_table: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    rollback_target: str | None = None

    def __post_init__(self) -> None:
        require_non_empty_text(self.comparison_id, field_name='comparison_id')
        require_non_empty_text(self.baseline_id, field_name='baseline_id')
        require_non_empty_text(self.challenger_id, field_name='challenger_id')
        require_non_empty_text(self.universe_id, field_name='universe_id')
        ensure_date(self.run_date, field_name='run_date')
        require_non_empty_text(self.cost_assumption_id, field_name='cost_assumption_id')
        ensure_mapping(self.coverage_summary, field_name='coverage_summary')
        ensure_mapping(self.metric_summary, field_name='metric_summary')
        require_non_empty_text(self.recommendation, field_name='recommendation')

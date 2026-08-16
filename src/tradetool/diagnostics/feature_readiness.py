from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradetool.data import ReadOnlySQLite, load_price_history_for_tickers
from tradetool.diagnostics.eligibility import build_eligibility_diagnostics
from tradetool.features import FeatureComputationResult, compute_raw_features


@dataclass(frozen=True, slots=True)
class FeatureReadinessRow:
    ticker: str
    feature_date: str
    feature_complete: bool
    feature_missing_reasons: tuple[str, ...]
    benchmark_ticker: str | None
    benchmark_alignment_status: str
    features: Mapping[str, float | int | bool | str | None]

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'feature_date': self.feature_date,
            'feature_complete': self.feature_complete,
            'feature_missing_reasons': list(self.feature_missing_reasons),
            'benchmark_ticker': self.benchmark_ticker,
            'benchmark_alignment_status': self.benchmark_alignment_status,
            **self.features,
        }


@dataclass(frozen=True, slots=True)
class FeatureReadinessDiagnosticsResult:
    db_path: Path
    universe_id: str
    universe_source: str
    benchmark_ticker: str | None
    price_table: str
    input_universe_count: int
    structural_eligible_count: int
    structural_rejected_count: int
    feature_row_count: int
    feature_complete_count: int
    feature_incomplete_count: int
    feature_missing_reason_counts: Mapping[str, int]
    generated_at_utc: str
    rows: tuple[FeatureReadinessRow, ...]

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'universe_id': self.universe_id,
            'universe_source': self.universe_source,
            'benchmark_ticker': self.benchmark_ticker,
            'input_universe_count': self.input_universe_count,
            'structural_eligible_count': self.structural_eligible_count,
            'structural_rejected_count': self.structural_rejected_count,
            'feature_row_count': self.feature_row_count,
            'feature_complete_count': self.feature_complete_count,
            'feature_incomplete_count': self.feature_incomplete_count,
            'feature_missing_reason_counts': dict(self.feature_missing_reason_counts),
            'generated_at_utc': self.generated_at_utc,
        }


def build_feature_readiness_diagnostics(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str | None = None,
    explicit_tickers: Sequence[str] | None = None,
    universe_csv_path: str | Path | None = None,
    price_table: str = 'price_history',
    min_history_rows: int = 252,
    freshness_tolerance_days: int = 0,
) -> FeatureReadinessDiagnosticsResult:
    eligibility = build_eligibility_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        explicit_tickers=explicit_tickers,
        universe_csv_path=universe_csv_path,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    eligible_tickers = tuple(row.ticker for row in eligibility.results if row.eligible)
    database = ReadOnlySQLite(db_path)
    tickers_to_load = list(eligible_tickers)
    if benchmark_ticker is not None:
        tickers_to_load.append(benchmark_ticker)
    loaded = load_price_history_for_tickers(
        database=database,
        tickers=tickers_to_load,
        price_table=eligibility.price_table,
        schema=eligibility.schema,
    )
    if benchmark_ticker is not None and benchmark_ticker not in loaded.rows_by_ticker:
        raise ValueError(f'Benchmark ticker "{benchmark_ticker}" is missing from price history.')

    rows: list[FeatureReadinessRow] = []
    reason_counter: Counter[str] = Counter()
    for ticker in eligible_tickers:
        feature_result = compute_raw_features(
            loaded.rows_by_ticker[ticker],
            benchmark_rows=None if benchmark_ticker is None else loaded.rows_by_ticker.get(benchmark_ticker),
            benchmark_ticker=benchmark_ticker,
        )
        rows.append(
            FeatureReadinessRow(
                ticker=ticker,
                feature_date=feature_result.feature_date.isoformat(),
                feature_complete=feature_result.feature_complete,
                feature_missing_reasons=feature_result.feature_missing_reasons,
                benchmark_ticker=feature_result.benchmark_ticker,
                benchmark_alignment_status=feature_result.benchmark_alignment_status,
                features=feature_result.features,
            )
        )
        reason_counter.update(feature_result.feature_missing_reasons)

    generated_at = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    feature_complete_count = sum(1 for row in rows if row.feature_complete)
    return FeatureReadinessDiagnosticsResult(
        db_path=Path(db_path).expanduser().resolve(),
        universe_id=universe_id,
        universe_source=eligibility.universe_source,
        benchmark_ticker=benchmark_ticker,
        price_table=eligibility.price_table,
        input_universe_count=eligibility.input_universe_count,
        structural_eligible_count=eligibility.eligible_count,
        structural_rejected_count=eligibility.rejected_count,
        feature_row_count=len(rows),
        feature_complete_count=feature_complete_count,
        feature_incomplete_count=len(rows) - feature_complete_count,
        feature_missing_reason_counts=dict(sorted(reason_counter.items())),
        generated_at_utc=generated_at,
        rows=tuple(rows),
    )

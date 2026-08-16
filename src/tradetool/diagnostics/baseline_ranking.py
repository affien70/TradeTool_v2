from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradetool.diagnostics.feature_readiness import build_feature_readiness_diagnostics
from tradetool.ranking import BASELINE_RANKING_ENGINE_ID, BaselineRankingInput, build_baseline_ranking


@dataclass(frozen=True, slots=True)
class BaselineRankingRow:
    ticker: str
    rank_date: str
    ranking_engine_id: str
    raw_rank: int
    raw_score: float
    contributions: Mapping[str, float]
    component_flags: Mapping[str, bool]
    input_fields: Mapping[str, float | int | bool | str | None]

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'rank_date': self.rank_date,
            'ranking_engine_id': self.ranking_engine_id,
            'raw_rank': self.raw_rank,
            'raw_score': self.raw_score,
            **{f'component_{key}': value for key, value in self.contributions.items()},
            **{f'flag_{key}': value for key, value in self.component_flags.items()},
            **self.input_fields,
        }


@dataclass(frozen=True, slots=True)
class BaselineRankingDiagnosticsResult:
    db_path: Path
    universe_id: str
    universe_source: str
    benchmark_ticker: str | None
    ranking_engine_id: str
    input_universe_count: int
    structural_eligible_count: int
    structural_rejected_count: int
    feature_complete_count: int
    feature_incomplete_count: int
    ranked_count: int
    generated_at_utc: str
    rows: tuple[BaselineRankingRow, ...]

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'universe_id': self.universe_id,
            'universe_source': self.universe_source,
            'benchmark_ticker': self.benchmark_ticker,
            'ranking_engine_id': self.ranking_engine_id,
            'input_universe_count': self.input_universe_count,
            'structural_eligible_count': self.structural_eligible_count,
            'structural_rejected_count': self.structural_rejected_count,
            'feature_complete_count': self.feature_complete_count,
            'feature_incomplete_count': self.feature_incomplete_count,
            'ranked_count': self.ranked_count,
            'generated_at_utc': self.generated_at_utc,
        }


def build_baseline_ranking_diagnostics(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str | None = None,
    explicit_tickers: Sequence[str] | None = None,
    universe_csv_path: str | Path | None = None,
    price_table: str = 'price_history',
    min_history_rows: int = 252,
    freshness_tolerance_days: int = 0,
) -> BaselineRankingDiagnosticsResult:
    feature_readiness = build_feature_readiness_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        benchmark_ticker=benchmark_ticker,
        explicit_tickers=explicit_tickers,
        universe_csv_path=universe_csv_path,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    complete_rows = [row for row in feature_readiness.rows if row.feature_complete]
    ranking_inputs = [
        BaselineRankingInput(
            ticker=row.ticker,
            rank_date=datetime.fromisoformat(row.feature_date).date(),
            features=row.features,
        )
        for row in complete_rows
    ]
    ranked = build_baseline_ranking(ranking_inputs)
    rows = tuple(
        BaselineRankingRow(
            ticker=result.ranked_candidate.ticker,
            rank_date=result.ranked_candidate.rank_date.isoformat(),
            ranking_engine_id=result.ranked_candidate.ranking_engine_id,
            raw_rank=result.ranked_candidate.raw_rank,
            raw_score=result.ranked_candidate.raw_score,
            contributions=result.contributions,
            component_flags=result.component_flags,
            input_fields=result.input_fields,
        )
        for result in ranked
    )
    return BaselineRankingDiagnosticsResult(
        db_path=Path(db_path).expanduser().resolve(),
        universe_id=universe_id,
        universe_source=feature_readiness.universe_source,
        benchmark_ticker=benchmark_ticker,
        ranking_engine_id=BASELINE_RANKING_ENGINE_ID,
        input_universe_count=feature_readiness.input_universe_count,
        structural_eligible_count=feature_readiness.structural_eligible_count,
        structural_rejected_count=feature_readiness.structural_rejected_count,
        feature_complete_count=feature_readiness.feature_complete_count,
        feature_incomplete_count=feature_readiness.feature_incomplete_count,
        ranked_count=len(rows),
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        rows=rows,
    )

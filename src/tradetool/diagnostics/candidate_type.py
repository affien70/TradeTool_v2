from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradetool.diagnostics.trade_policy import TradePolicyDiagnosticsResult, build_trade_policy_diagnostics
from tradetool.policy import (
    CANDIDATE_TYPE_ENGINE_ID,
    CandidateTypeDiagnosticsRow,
    CandidateTypeInputRow,
    apply_candidate_type_diagnostics,
    summarize_candidate_types,
)


@dataclass(frozen=True, slots=True)
class CandidateTypeDiagnosticsResult:
    policy: TradePolicyDiagnosticsResult
    classification_engine_id: str
    generated_at_utc: str
    rows: tuple[CandidateTypeDiagnosticsRow, ...]
    candidate_type_counts: Mapping[str, int]
    trade_signal_counts: Mapping[str, int]

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'universe_id': self.policy.ranking.universe_id,
            'universe_source': self.policy.ranking.universe_source,
            'benchmark_ticker': self.policy.ranking.benchmark_ticker,
            'ranking_engine_id': self.policy.ranking.ranking_engine_id,
            'policy_engine_id': self.policy.policy_engine_id,
            'classification_engine_id': self.classification_engine_id,
            'input_universe_count': self.policy.ranking.input_universe_count,
            'ranked_count': self.policy.ranking.ranked_count,
            'candidate_type_row_count': len(self.rows),
            'candidate_type_counts': dict(self.candidate_type_counts),
            'trade_signal_counts': dict(self.trade_signal_counts),
            'generated_at_utc': self.generated_at_utc,
        }


def build_candidate_type_diagnostics(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str | None = None,
    explicit_tickers: Sequence[str] | None = None,
    universe_csv_path: str | Path | None = None,
    price_table: str = 'price_history',
    min_history_rows: int = 252,
    freshness_tolerance_days: int = 0,
) -> CandidateTypeDiagnosticsResult:
    policy = build_trade_policy_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        benchmark_ticker=benchmark_ticker,
        explicit_tickers=explicit_tickers,
        universe_csv_path=universe_csv_path,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    rows = apply_candidate_type_diagnostics(
        tuple(
            CandidateTypeInputRow(
                ticker=row.ticker,
                rank_date=row.rank_date,
                ranking_engine_id=row.ranking_engine_id,
                policy_engine_id=row.policy_engine_id,
                raw_rank=row.raw_rank,
                raw_score=row.raw_score,
                trade_signal=row.trade_signal,
                policy_pass=row.policy_pass,
                policy_reasons=row.policy_reasons,
                policy_warnings=row.policy_warnings,
                above_sma50=row.above_sma50,
                above_sma200=row.above_sma200,
                positive_return_3m=row.positive_return_3m,
                positive_return_6m=row.positive_return_6m,
                positive_rs_3m=row.positive_rs_3m,
                positive_rs_6m=row.positive_rs_6m,
                acceptable_drawdown=row.acceptable_drawdown,
                acceptable_volatility=row.acceptable_volatility,
                acceptable_traded_value=row.acceptable_traded_value,
                moderate_stretch=row.moderate_stretch,
                severe_stretch=row.severe_stretch,
                drawdown_252=row.drawdown_252,
                volatility_63=row.volatility_63,
                average_traded_value_20=row.average_traded_value_20,
                distance_to_sma50=row.distance_to_sma50,
                distance_to_sma200=row.distance_to_sma200,
            )
            for row in policy.rows
        )
    )
    summary = summarize_candidate_types(rows)
    return CandidateTypeDiagnosticsResult(
        policy=policy,
        classification_engine_id=CANDIDATE_TYPE_ENGINE_ID,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        rows=rows,
        candidate_type_counts=summary['candidate_type_counts'],
        trade_signal_counts=summary['trade_signal_counts'],
    )


def write_candidate_type_outputs(*, result: CandidateTypeDiagnosticsResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(result.rows[0].to_dict().keys()) if result.rows else [
        'ticker', 'rank_date', 'ranking_engine_id', 'policy_engine_id', 'classification_engine_id', 'raw_rank', 'raw_score', 'trade_signal', 'candidate_type'
    ]
    with (out_dir / 'candidate_type_diagnostics.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in result.rows:
            writer.writerow(row.to_dict())
    (out_dir / 'candidate_type_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'candidate_type_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')


def _render_summary_markdown(result: CandidateTypeDiagnosticsResult) -> str:
    lines = [
        '# Candidate Type Diagnostics Summary',
        '',
        f'- Universe: {result.policy.ranking.universe_id}',
        f'- Universe source: {result.policy.ranking.universe_source}',
        f'- Benchmark: {result.policy.ranking.benchmark_ticker or "not requested"}',
        f'- Ranking engine id: {result.policy.ranking.ranking_engine_id}',
        f'- Policy engine id: {result.policy.policy_engine_id}',
        f'- Classification engine id: {result.classification_engine_id}',
        f'- Input universe count: {result.policy.ranking.input_universe_count}',
        f'- Ranked count: {result.policy.ranking.ranked_count}',
        f'- Candidate type row count: {len(result.rows)}',
        f'- Generated at: {result.generated_at_utc}',
        '',
        '## Candidate type counts',
        '',
    ]
    for candidate_type, count in sorted(result.candidate_type_counts.items()):
        lines.append(f'- {candidate_type}: {count}')
    lines.extend(['', '## Trade signal counts', ''])
    for signal, count in sorted(result.trade_signal_counts.items()):
        lines.append(f'- {signal}: {count}')
    lines.extend(['', '## Top 20', ''])
    for row in result.rows[:20]:
        reasons = ', '.join(row.classification_reasons)
        warnings = ', '.join(row.classification_warnings) if row.classification_warnings else 'none'
        lines.append(
            f'- {row.raw_rank}. {row.ticker}: {row.trade_signal.value} | {row.candidate_type.value} | reasons={reasons} | warnings={warnings}'
        )
    return '\n'.join(lines) + '\n'

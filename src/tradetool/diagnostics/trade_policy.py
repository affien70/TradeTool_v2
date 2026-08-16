from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradetool.diagnostics.baseline_ranking import BaselineRankingDiagnosticsResult, build_baseline_ranking_diagnostics
from tradetool.policy import (
    TRADE_POLICY_ENGINE_ID,
    TradePolicyDiagnosticsRow,
    TradePolicyInputRow,
    apply_trade_policy_diagnostics,
    summarize_trade_policy,
)


@dataclass(frozen=True, slots=True)
class TradePolicyDiagnosticsResult:
    ranking: BaselineRankingDiagnosticsResult
    policy_engine_id: str
    generated_at_utc: str
    rows: tuple[TradePolicyDiagnosticsRow, ...]
    signal_counts: Mapping[str, int]
    policy_pass_count: int

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'universe_id': self.ranking.universe_id,
            'universe_source': self.ranking.universe_source,
            'benchmark_ticker': self.ranking.benchmark_ticker,
            'ranking_engine_id': self.ranking.ranking_engine_id,
            'policy_engine_id': self.policy_engine_id,
            'input_universe_count': self.ranking.input_universe_count,
            'structural_eligible_count': self.ranking.structural_eligible_count,
            'structural_rejected_count': self.ranking.structural_rejected_count,
            'feature_complete_count': self.ranking.feature_complete_count,
            'ranked_count': self.ranking.ranked_count,
            'policy_row_count': len(self.rows),
            'policy_pass_count': self.policy_pass_count,
            'signal_counts': dict(self.signal_counts),
            'generated_at_utc': self.generated_at_utc,
        }


def build_trade_policy_diagnostics(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str | None = None,
    explicit_tickers: Sequence[str] | None = None,
    universe_csv_path: str | Path | None = None,
    price_table: str = 'price_history',
    min_history_rows: int = 252,
    freshness_tolerance_days: int = 0,
) -> TradePolicyDiagnosticsResult:
    ranking = build_baseline_ranking_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        benchmark_ticker=benchmark_ticker,
        explicit_tickers=explicit_tickers,
        universe_csv_path=universe_csv_path,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    rows = apply_trade_policy_diagnostics(
        tuple(
            TradePolicyInputRow(
                ticker=row.ticker,
                rank_date=row.rank_date,
                ranking_engine_id=row.ranking_engine_id,
                raw_rank=row.raw_rank,
                raw_score=row.raw_score,
                input_fields=row.input_fields,
            )
            for row in ranking.rows
        )
    )
    summary = summarize_trade_policy(rows)
    return TradePolicyDiagnosticsResult(
        ranking=ranking,
        policy_engine_id=TRADE_POLICY_ENGINE_ID,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        rows=rows,
        signal_counts=summary['signal_counts'],
        policy_pass_count=int(summary['policy_pass_count']),
    )


def write_trade_policy_outputs(*, result: TradePolicyDiagnosticsResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(result.rows[0].to_dict().keys()) if result.rows else [
        'ticker', 'rank_date', 'ranking_engine_id', 'policy_engine_id', 'raw_rank', 'raw_score', 'trade_signal'
    ]
    with (out_dir / 'trade_policy_diagnostics.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in result.rows:
            writer.writerow(row.to_dict())
    (out_dir / 'trade_policy_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'trade_policy_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')


def _render_summary_markdown(result: TradePolicyDiagnosticsResult) -> str:
    lines = [
        '# Trade Policy Diagnostics Summary',
        '',
        f'- Universe: {result.ranking.universe_id}',
        f'- Universe source: {result.ranking.universe_source}',
        f'- Benchmark: {result.ranking.benchmark_ticker or "not requested"}',
        f'- Ranking engine id: {result.ranking.ranking_engine_id}',
        f'- Policy engine id: {result.policy_engine_id}',
        f'- Input universe count: {result.ranking.input_universe_count}',
        f'- Structural eligible count: {result.ranking.structural_eligible_count}',
        f'- Structural rejected count: {result.ranking.structural_rejected_count}',
        f'- Feature complete count: {result.ranking.feature_complete_count}',
        f'- Ranked count: {result.ranking.ranked_count}',
        f'- Policy row count: {len(result.rows)}',
        f'- Policy pass count: {result.policy_pass_count}',
        f'- Generated at: {result.generated_at_utc}',
        '',
        '## Signal counts',
        '',
    ]
    for signal, count in sorted(result.signal_counts.items()):
        lines.append(f'- {signal}: {count}')
    lines.extend(['', '## Top 20', ''])
    for row in result.rows[:20]:
        reasons = ', '.join(row.policy_reasons)
        warnings = ', '.join(row.policy_warnings) if row.policy_warnings else 'none'
        lines.append(
            f'- {row.raw_rank}. {row.ticker}: {row.trade_signal.value} | score={row.raw_score:.2f} | reasons={reasons} | warnings={warnings}'
        )
    return '\n'.join(lines) + '\n'

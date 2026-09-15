from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradetool.diagnostics.challenger_no_drawdown_comparison import CHALLENGER_ID
from tradetool.diagnostics.incumbent_baseline import BASELINE_ID

DECISION_RECOMMENDATION = 'keep_incumbent_and_design_next_challenger'
NEXT_RECOMMENDED_ACTION = 'design_next_challenger_without_drawdown_damage'
EXPECTED_REPORT_FILES = (
    'cross_universe_decision_summary.md',
    'cross_universe_decision_summary.json',
    'cross_universe_result_matrix.csv',
    'cross_universe_decision.csv',
)


@dataclass(frozen=True, slots=True)
class CrossUniverseResult:
    universe_id: str
    benchmark_ticker: str
    rebalance_start_date: str
    rebalance_end_date: str
    rebalance_date_count: int
    ranked_total: int
    ranked_min: int
    ranked_max: int
    incumbent_20d_net_excess: float
    incumbent_60d_net_excess: float
    challenger_20d_net_excess: float
    challenger_60d_net_excess: float
    spread_20d_net_excess: float
    spread_60d_net_excess: float
    v2_raw_top10_60d_net_excess: float | None

    def to_row(self) -> dict[str, object]:
        return {
            'universe_id': self.universe_id,
            'benchmark_ticker': self.benchmark_ticker,
            'rebalance_start_date': self.rebalance_start_date,
            'rebalance_end_date': self.rebalance_end_date,
            'rebalance_date_count': self.rebalance_date_count,
            'ranked_total': self.ranked_total,
            'ranked_min': self.ranked_min,
            'ranked_max': self.ranked_max,
            'incumbent_id': BASELINE_ID,
            'challenger_id': CHALLENGER_ID,
            'incumbent_20d_net_excess': self.incumbent_20d_net_excess,
            'incumbent_60d_net_excess': self.incumbent_60d_net_excess,
            'challenger_20d_net_excess': self.challenger_20d_net_excess,
            'challenger_60d_net_excess': self.challenger_60d_net_excess,
            'spread_20d_net_excess': self.spread_20d_net_excess,
            'spread_60d_net_excess': self.spread_60d_net_excess,
            'v2_raw_top10_60d_net_excess': self.v2_raw_top10_60d_net_excess,
        }


@dataclass(frozen=True, slots=True)
class CrossUniverseDecisionResult:
    incumbent_id: str
    challenger_id: str
    decision_recommendation: str
    next_recommended_action: str
    result_rows: tuple[CrossUniverseResult, ...]
    evidence_source: tuple[str, ...]
    decision_reasons: tuple[str, ...]
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'incumbent_id': self.incumbent_id,
            'challenger_id': self.challenger_id,
            'decision_recommendation': self.decision_recommendation,
            'next_recommended_action': self.next_recommended_action,
            'universes_tested': [row.universe_id for row in self.result_rows],
            'result_matrix': [row.to_row() for row in self.result_rows],
            'evidence_source': list(self.evidence_source),
            'decision_reasons': list(self.decision_reasons),
            'leakage_controls': {
                'uses_existing_validated_diagnostic_results': True,
                'reruns_yahoo': False,
                'ranking_changed': False,
                'policy_changed': False,
                'screener_ui_changed': False,
                'ml_or_holdings_logic_changed': False,
                'production_database_write_required': False,
                'tuning_applied': False,
            },
            'generated_at_utc': self.generated_at_utc,
        }


def build_cross_universe_decision() -> CrossUniverseDecisionResult:
    rows = (
        CrossUniverseResult(
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            rebalance_start_date='2023-01-31',
            rebalance_end_date='2026-05-29',
            rebalance_date_count=41,
            ranked_total=10516,
            ranked_min=241,
            ranked_max=271,
            incumbent_20d_net_excess=0.004282,
            incumbent_60d_net_excess=0.009132,
            challenger_20d_net_excess=0.002485,
            challenger_60d_net_excess=0.021144,
            spread_20d_net_excess=-0.001797,
            spread_60d_net_excess=0.012012,
            v2_raw_top10_60d_net_excess=0.006020,
        ),
        CrossUniverseResult(
            universe_id='SP500',
            benchmark_ticker='^GSPC',
            rebalance_start_date='2023-01-31',
            rebalance_end_date='2026-05-29',
            rebalance_date_count=41,
            ranked_total=20199,
            ranked_min=490,
            ranked_max=496,
            incumbent_20d_net_excess=0.049203,
            incumbent_60d_net_excess=0.164219,
            challenger_20d_net_excess=0.048023,
            challenger_60d_net_excess=0.160040,
            spread_20d_net_excess=-0.001180,
            spread_60d_net_excess=-0.004179,
            v2_raw_top10_60d_net_excess=-0.018679,
        ),
    )
    return CrossUniverseDecisionResult(
        incumbent_id=BASELINE_ID,
        challenger_id=CHALLENGER_ID,
        decision_recommendation=DECISION_RECOMMENDATION,
        next_recommended_action=NEXT_RECOMMENDED_ACTION,
        result_rows=rows,
        evidence_source=(
            'Phase 5k NORWAY_V2 extended rerun after non-OSE loader fix',
            'Phase 5l SP500 cross-universe validation after non-OSE loader fix',
        ),
        decision_reasons=(
            'Incumbent remains the more robust cross-universe baseline.',
            'No-drawdown challenger improves over the failed drawdown-gated challenger but does not consistently beat the incumbent.',
            'SP500 evidence shows the incumbent beating no-drawdown on both 20d and 60d net excess.',
            'NORWAY evidence shows no-drawdown ahead at 60d but behind at 20d.',
            'v2 raw top10 remains weak in SP500, so the next challenger should target filter damage without promoting current v2 ranking.',
        ),
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_cross_universe_decision_outputs(*, result: CrossUniverseDecisionResult, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'cross_universe_decision_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'cross_universe_decision_summary.md').write_text(_render_markdown(result), encoding='utf-8')
    _write_csv(path / 'cross_universe_result_matrix.csv', (row.to_row() for row in result.result_rows))
    _write_csv(path / 'cross_universe_decision.csv', (_decision_row(result),))


def _decision_row(result: CrossUniverseDecisionResult) -> dict[str, object]:
    return {
        'decision_recommendation': result.decision_recommendation,
        'next_recommended_action': result.next_recommended_action,
        'incumbent_id': result.incumbent_id,
        'challenger_id': result.challenger_id,
        'decision_reasons': '; '.join(result.decision_reasons),
    }


def _render_markdown(result: CrossUniverseDecisionResult) -> str:
    lines = [
        '# Cross-Universe Incumbent Decision',
        '',
        f'- Decision: `{result.decision_recommendation}`',
        f'- Next recommended action: `{result.next_recommended_action}`',
        f'- Incumbent: `{result.incumbent_id}`',
        f'- Challenger reviewed: `{result.challenger_id}`',
        '',
        '## Result Matrix',
        '',
        '| Universe | Benchmark | Ranked total/range | Incumbent 20d | Incumbent 60d | Challenger 20d | Challenger 60d | Spread 20d | Spread 60d | v2 raw top10 60d |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for row in result.result_rows:
        lines.append(
            f'| {row.universe_id} | {row.benchmark_ticker} | {row.ranked_total} / {row.ranked_min}-{row.ranked_max} | '
            f'{row.incumbent_20d_net_excess:.6f} | {row.incumbent_60d_net_excess:.6f} | '
            f'{row.challenger_20d_net_excess:.6f} | {row.challenger_60d_net_excess:.6f} | '
            f'{row.spread_20d_net_excess:.6f} | {row.spread_60d_net_excess:.6f} | '
            f'{"" if row.v2_raw_top10_60d_net_excess is None else f"{row.v2_raw_top10_60d_net_excess:.6f}"} |'
        )
    lines.extend(['', '## Decision Reasons'])
    lines.extend(f'- {reason}' for reason in result.decision_reasons)
    lines.extend(['', '## Evidence Source'])
    lines.extend(f'- {source}' for source in result.evidence_source)
    lines.extend(
        [
            '',
            '## Guardrails',
            '- Diagnostic decision report only; no ranking, policy, Screener UI, ML, Holdings, or production DB change.',
        ]
    )
    return '\n'.join(lines) + '\n'


def _write_csv(path: Path, rows) -> None:
    fieldnames: list[str] = []
    normalized = []
    for source in rows:
        row = dict(source)
        normalized.append(row)
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = ['empty']
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in normalized:
            writer.writerow(row)

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradetool.diagnostics.monthly_holdout_backtest import ROUND_TRIP_COST_RATE

BASELINE_ID = 'incumbent_naive_rs_6m_top_10_v0'
BENCHMARK_TICKER = 'OSEBX.OL'
CLOSE_SOURCE = 'adjusted_close'
SELECTION_LIMIT = 10
SELECTION_FIELD = 'relative_strength_6m'
DECISION_NEXT_ACTION = 'use_incumbent_baseline_in_next_challenger_comparison'
EXPECTED_REPORT_FILES = (
    'incumbent_baseline_decision.csv',
    'incumbent_baseline_definition.csv',
    'incumbent_baseline_summary.json',
    'incumbent_baseline_summary.md',
)


@dataclass(frozen=True, slots=True)
class IncumbentBaselineDefinitionResult:
    baseline_id: str
    universe_definition: str
    selection_rule: str
    benchmark_ticker: str
    close_source: str
    transaction_cost_round_trip: float
    intended_use: str
    selected_because: str
    evidence_source: tuple[str, ...]
    limitations: tuple[str, ...]
    future_challenger_gate: str
    decision_recommendation: str
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'baseline_id': self.baseline_id,
            'universe_definition': self.universe_definition,
            'selection_rule': self.selection_rule,
            'benchmark_ticker': self.benchmark_ticker,
            'close_source': self.close_source,
            'transaction_cost_round_trip': self.transaction_cost_round_trip,
            'intended_use': self.intended_use,
            'selected_because': self.selected_because,
            'evidence_source': list(self.evidence_source),
            'limitations': list(self.limitations),
            'future_challenger_gate': self.future_challenger_gate,
            'decision_recommendation': self.decision_recommendation,
            'leakage_controls': {
                'testing_baseline_only': True,
                'screener_ui_changed': False,
                'ranking_formula_changed': False,
                'policy_thresholds_changed': False,
                'candidate_type_rules_changed': False,
                'ml_or_holdings_logic_changed': False,
                'production_database_write_required': False,
                'tuning_applied': False,
            },
            'generated_at_utc': self.generated_at_utc,
        }


def build_incumbent_baseline_definition() -> IncumbentBaselineDefinitionResult:
    return IncumbentBaselineDefinitionResult(
        baseline_id=BASELINE_ID,
        universe_definition='feature-complete stocks from the same diagnostic snapshot',
        selection_rule=(
            'sort by relative_strength_6m descending, tie-break by ticker ascending, '
            'select top 10'
        ),
        benchmark_ticker=BENCHMARK_TICKER,
        close_source=CLOSE_SOURCE,
        transaction_cost_round_trip=ROUND_TRIP_COST_RATE,
        intended_use='testing baseline only, not user-facing production screener',
        selected_because=(
            'Phase 5g full-universe monthly holdout identified naive_rs_6m_top_10 '
            'as the dominant naive group against current v2 groups.'
        ),
        evidence_source=(
            'Phase 5f baseline/naive comparison report',
            'Phase 5g full NORWAY_V2 naive dominance rerun',
        ),
        limitations=(
            'Uses current universe membership and remains subject to survivorship-bias limitations.',
            'Uses simple 6m relative strength only; no trade policy, candidate type, ML, or Holdings adjustment.',
            'Approved for future testing/comparison reports only.',
        ),
        future_challenger_gate=(
            'Future challengers must beat incumbent_naive_rs_6m_top_10_v0 on the same '
            'universe, benchmark, adjusted-close source, windows, and transaction-cost convention.'
        ),
        decision_recommendation=DECISION_NEXT_ACTION,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def select_incumbent_baseline(rows: tuple[dict[str, object], ...], *, limit: int = SELECTION_LIMIT) -> tuple[dict[str, object], ...]:
    valid_rows = tuple(row for row in rows if _as_optional_float(row.get(SELECTION_FIELD)) is not None)
    return tuple(sorted(valid_rows, key=lambda row: (-_as_float(row[SELECTION_FIELD]), str(row['ticker'])))[:limit])


def write_incumbent_baseline_outputs(*, result: IncumbentBaselineDefinitionResult, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'incumbent_baseline_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'incumbent_baseline_summary.md').write_text(_render_markdown(result), encoding='utf-8')
    _write_csv(path / 'incumbent_baseline_definition.csv', (_definition_row(result),))
    _write_csv(
        path / 'incumbent_baseline_decision.csv',
        [{'decision_recommendation': result.decision_recommendation}],
    )


def _definition_row(result: IncumbentBaselineDefinitionResult) -> dict[str, object]:
    return {
        'baseline_id': result.baseline_id,
        'universe_definition': result.universe_definition,
        'selection_field': SELECTION_FIELD,
        'selection_sort_direction': 'descending',
        'tie_breaker': 'ticker ascending',
        'selection_limit': SELECTION_LIMIT,
        'benchmark_ticker': result.benchmark_ticker,
        'close_source': result.close_source,
        'transaction_cost_round_trip': result.transaction_cost_round_trip,
        'intended_use': result.intended_use,
        'future_challenger_gate': result.future_challenger_gate,
    }


def _render_markdown(result: IncumbentBaselineDefinitionResult) -> str:
    lines = [
        '# Incumbent Baseline Definition',
        '',
        f'- Baseline ID: `{result.baseline_id}`',
        f'- Universe: {result.universe_definition}',
        f'- Selection rule: {result.selection_rule}',
        f'- Benchmark: `{result.benchmark_ticker}`',
        f'- Close source: `{result.close_source}`',
        f'- Round-trip transaction cost: {result.transaction_cost_round_trip}',
        f'- Intended use: {result.intended_use}',
        '',
        '## Why Selected',
        result.selected_because,
        '',
        '## Evidence Source',
    ]
    lines.extend(f'- {source}' for source in result.evidence_source)
    lines.extend(['', '## Limitations'])
    lines.extend(f'- {limitation}' for limitation in result.limitations)
    lines.extend(
        [
            '',
            '## Future Challenger Gate',
            result.future_challenger_gate,
            '',
            '## Recommended Next Action',
            f'`{result.decision_recommendation}`',
            '',
            '## Guardrails',
            '- No tuning, ranking formula change, policy threshold change, Screener UI change, ML/Holdings change, or production DB write.',
        ]
    )
    return '\n'.join(lines) + '\n'


def _write_csv(path: Path, rows) -> None:
    fieldnames: list[str] = []
    normalized = []
    for source in rows:
        row = {key: _csv_value(value) for key, value in source.items()}
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


def _csv_value(value: object) -> object:
    if isinstance(value, (list, tuple, set)):
        return ';'.join(str(item) for item in value)
    return value


def _as_float(value: object) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


def _as_optional_float(value: object) -> float | None:
    if value is None or value == '':
        return None
    parsed = _as_float(value)
    if not math.isfinite(parsed):
        return None
    return parsed

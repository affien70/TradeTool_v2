from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradetool.policy import CANDIDATE_TYPE_ENGINE_ID, TRADE_POLICY_ENGINE_ID
from tradetool.ranking import BASELINE_RANKING_ENGINE_ID

CURRENT_VALIDATED_PRICE_TABLE = 'price_history_v2'
CURRENT_VALIDATED_CLOSE_SOURCE = 'adjusted_close'
DECISION_IMPLEMENT_SNAPSHOT = 'implement_minimal_holdout_snapshot_engine'
DECISION_FILL_PRIMITIVES = 'fill_missing_backtest_primitives_first'
DECISION_FIX_DATA = 'fix_data_history_before_backtest'
DECISION_COMPARE_BASELINE = 'compare_against_phase1_baselines_first'
DECISION_BLOCKED_EVIDENCE = 'blocked_insufficient_evidence'
EXPECTED_REPORT_FILES = (
    'available_evidence_inventory.csv',
    'decision.csv',
    'holdout_backtest_feasibility_summary.json',
    'holdout_backtest_feasibility_summary.md',
    'leakage_risk_register.csv',
    'proposed_backtest_protocol.csv',
    'required_engine_gaps.csv',
)


@dataclass(frozen=True, slots=True)
class HoldoutBacktestFeasibilityResult:
    universe_id: str
    benchmark_ticker: str
    ranking_engine_id: str
    trade_policy_engine_id: str
    candidate_type_engine_id: str
    validated_data_source: str
    validated_benchmark: str
    validated_close_input_source: str
    latest_phase4x_summary: dict[str, object]
    evidence_inventory_rows: tuple[dict[str, object], ...]
    proposed_protocol_rows: tuple[dict[str, object], ...]
    leakage_risk_rows: tuple[dict[str, object], ...]
    engine_gap_rows: tuple[dict[str, object], ...]
    decision_recommendation: str
    decision_reasons: tuple[str, ...]
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'universe_id': self.universe_id,
            'benchmark_ticker': self.benchmark_ticker,
            'current_state': {
                'ranking_engine_id': self.ranking_engine_id,
                'trade_policy_engine_id': self.trade_policy_engine_id,
                'candidate_type_engine_id': self.candidate_type_engine_id,
                'validated_data_source': self.validated_data_source,
                'validated_benchmark': self.validated_benchmark,
                'validated_close_input_source': self.validated_close_input_source,
                'latest_phase4x_summary': self.latest_phase4x_summary,
            },
            'available_evidence': list(self.evidence_inventory_rows),
            'proposed_protocol': list(self.proposed_protocol_rows),
            'leakage_risks': list(self.leakage_risk_rows),
            'engine_gaps': list(self.engine_gap_rows),
            'decision_recommendation': self.decision_recommendation,
            'decision_reasons': list(self.decision_reasons),
            'generated_at_utc': self.generated_at_utc,
        }


def build_holdout_backtest_feasibility(
    *,
    universe_id: str,
    benchmark_ticker: str,
    project_root: str | Path = '.',
) -> HoldoutBacktestFeasibilityResult:
    root = Path(project_root).expanduser().resolve()
    evidence_rows = build_evidence_inventory(project_root=root)
    protocol_rows = build_proposed_protocol(universe_id=universe_id, benchmark_ticker=benchmark_ticker)
    leakage_rows = build_leakage_risk_register()
    engine_gap_rows = build_engine_gap_assessment(project_root=root)
    decision, reasons = recommend_next_action(evidence_rows=evidence_rows, engine_gap_rows=engine_gap_rows)
    return HoldoutBacktestFeasibilityResult(
        universe_id=universe_id,
        benchmark_ticker=benchmark_ticker,
        ranking_engine_id=BASELINE_RANKING_ENGINE_ID,
        trade_policy_engine_id=TRADE_POLICY_ENGINE_ID,
        candidate_type_engine_id=CANDIDATE_TYPE_ENGINE_ID,
        validated_data_source=CURRENT_VALIDATED_PRICE_TABLE,
        validated_benchmark=benchmark_ticker,
        validated_close_input_source=CURRENT_VALIDATED_CLOSE_SOURCE,
        latest_phase4x_summary={
            'stock_ticker_count': 293,
            'fetched_ticker_count': 289,
            'ranked_count': 274,
            'buy_count': 13,
            'watch_count': 4,
            'serious_buy_watch_red_flag_count': 0,
        },
        evidence_inventory_rows=evidence_rows,
        proposed_protocol_rows=protocol_rows,
        leakage_risk_rows=leakage_rows,
        engine_gap_rows=engine_gap_rows,
        decision_recommendation=decision,
        decision_reasons=reasons,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def build_evidence_inventory(*, project_root: Path) -> tuple[dict[str, object], ...]:
    phase1_dir = project_root / 'evidence' / 'v1_baseline' / '20260622T200335Z'
    rows = [
        _evidence_row('phase1_ose_baseline_top20', phase1_dir / 'ose_method_comparison_top20.csv'),
        _evidence_row('phase1_focus_ticker_baseline', phase1_dir / 'focus_ticker_baseline.csv'),
        _evidence_row('phase1_artifact_inventory', phase1_dir / 'artifact_inventory.json'),
        _evidence_row('phase1_universe_coverage', phase1_dir / 'universe_coverage.json'),
        _evidence_row('phase1_limitations', phase1_dir / 'limitations.md'),
        _code_row('current_candidate_quality_comparison_code', project_root / 'src' / 'tradetool' / 'diagnostics' / 'candidate_quality_comparison.py'),
        _code_row('current_v2_market_data_validation_code', project_root / 'src' / 'tradetool' / 'diagnostics' / 'market_data_v2_readiness.py'),
        _code_row('market_data_temp_write_validation_code', project_root / 'src' / 'tradetool' / 'diagnostics' / 'market_data_write_test.py'),
        _evidence_from_baseline_methods(
            'old_main_momentum_evidence',
            phase1_dir / 'ose_method_comparison_top20.csv',
            'main_momentum',
        ),
        _evidence_from_baseline_methods(
            'parked_practical_first_evidence',
            phase1_dir / 'ose_method_comparison_top20.csv',
            'parked_practical_first',
        ),
        _evidence_from_baseline_methods('raw_ml_evidence', phase1_dir / 'ose_method_comparison_top20.csv', 'raw_ml'),
        _backtest_engine_row(project_root),
    ]
    return tuple(rows)


def build_proposed_protocol(*, universe_id: str, benchmark_ticker: str) -> tuple[dict[str, object], ...]:
    selection_buckets = (
        ('raw_top_n_10', 'top 10 by raw rank'),
        ('raw_top_n_20', 'top 20 by raw rank'),
        ('buy_only', 'trade_signal == BUY'),
        ('buy_watch', 'trade_signal in BUY/WATCH'),
        ('review_bucket', 'trade_signal == REVIEW diagnostic comparison'),
        ('avoid_bucket', 'trade_signal == AVOID bottom/risk comparison'),
    )
    rows: list[dict[str, object]] = []
    for forward_window in (20, 60):
        for bucket_id, bucket_definition in selection_buckets:
            rows.append(
                {
                    'universe_id': universe_id,
                    'benchmark_ticker': benchmark_ticker,
                    'data_source': CURRENT_VALIDATED_PRICE_TABLE,
                    'close_source': CURRENT_VALIDATED_CLOSE_SOURCE,
                    'rebalance_frequency': 'monthly',
                    'rebalance_date_rule': 'month_end_or_first_trading_day_after_month_end',
                    'selection_bucket': bucket_id,
                    'selection_definition': bucket_definition,
                    'forward_window_trading_days': forward_window,
                    'gross_return_metrics': 'absolute_forward_return; benchmark_forward_return; excess_forward_return; hit_rate_vs_benchmark; top_vs_bottom_spread; median_return; drawdown_proxy',
                    'net_return_metrics': 'same metrics after simple transaction cost',
                    'turnover_metric': 'ticker entry/exit turnover per rebalance date',
                    'missing_data_metrics': 'skipped_count; missing_entry_count; missing_exit_count',
                    'cost_assumption': '10 bps one-way transaction cost per position change',
                    'comparison_targets': 'v2_baseline_ranking_policy; phase1_baselines_when_date_compatible; raw_ml_only_if_valid_date_compatible_evidence_exists',
                    'output_granularity': 'per_rebalance_date; per_ticker_pick; aggregate_summary',
                }
            )
    return tuple(rows)


def build_leakage_risk_register() -> tuple[dict[str, object], ...]:
    controls = (
        ('point_in_time_features', 'features use only data <= rebalance date', 'required'),
        ('forward_label_start', 'forward returns start after rebalance date', 'required'),
        ('adjusted_close_consistency', 'adjusted_close used consistently for stock and benchmark returns', 'required'),
        ('benchmark_window_alignment', 'benchmark forward return uses the same date window as candidate forward return', 'required'),
        ('survivorship_membership', 'current NORWAY_V2 membership may be survivorship-biased unless historical membership is added', 'documented_limitation'),
        ('no_candidate_quality_training_target', 'current candidate-quality report cannot become a training target', 'required'),
        ('no_manual_focus_adjustment', 'focus tickers are audit rows only, not selection overrides', 'required'),
        ('no_holdings_adjustment', 'ownership/holdings must not affect holdout selection', 'required'),
        ('invalid_row_no_lookahead', 'skipped invalid rows cannot reveal future price availability to selection', 'required'),
    )
    return tuple(
        {
            'risk_id': risk_id,
            'control': control,
            'status': status,
            'required_before_backtest': status == 'required',
        }
        for risk_id, control, status in controls
    )


def build_engine_gap_assessment(*, project_root: Path) -> tuple[dict[str, object], ...]:
    backtest_engine_present = _has_backtest_engine(project_root)
    specs = (
        ('load_historical_windows_as_of_date', 'partial', 'price readers load ticker history, but no explicit as-of window API is present'),
        ('compute_feature_rows_as_of_date', 'missing', 'feature readiness computes latest/current rows only'),
        ('run_ranking_as_of_date', 'ready', 'ranking is pure once point-in-time feature rows are supplied'),
        ('apply_trade_policy_as_of_date', 'ready', 'policy is pure once point-in-time ranked rows and fields are supplied'),
        ('assign_candidate_type_as_of_date', 'ready', 'candidate type is pure once point-in-time policy rows are supplied'),
        ('calculate_forward_returns', 'missing', 'no forward-return primitive was found'),
        ('aggregate_monthly_backtest_results', 'missing' if not backtest_engine_present else 'partial', 'no reusable holdout aggregation engine was found' if not backtest_engine_present else 'backtest references exist but need scope review'),
        ('compare_against_benchmark', 'partial', 'benchmark alignment exists for features, not for forward return windows'),
        ('write_report_outputs', 'ready', 'diagnostic modules already write CSV/JSON/Markdown reports'),
    )
    return tuple({'capability': name, 'status': status, 'evidence': evidence} for name, status, evidence in specs)


def recommend_next_action(
    *,
    evidence_rows: tuple[dict[str, object], ...],
    engine_gap_rows: tuple[dict[str, object], ...],
) -> tuple[str, tuple[str, ...]]:
    evidence_by_id = {str(row['evidence_id']): row for row in evidence_rows}
    if not evidence_by_id.get('phase1_ose_baseline_top20', {}).get('available'):
        return DECISION_COMPARE_BASELINE, ('phase1_ose_baseline_top20_missing',)
    if any(row['status'] == 'unsafe_without_fix' for row in engine_gap_rows):
        return DECISION_FILL_PRIMITIVES, ('one_or_more_primitives_are_unsafe_without_fix',)
    missing_core = {
        str(row['capability'])
        for row in engine_gap_rows
        if row['status'] == 'missing'
    }
    expected_missing = {
        'compute_feature_rows_as_of_date',
        'calculate_forward_returns',
        'aggregate_monthly_backtest_results',
    }
    if missing_core and missing_core.issubset(expected_missing):
        return DECISION_IMPLEMENT_SNAPSHOT, ('historical_as_of_and_forward_return_primitives_missing_but_feasible',)
    if missing_core:
        return DECISION_FILL_PRIMITIVES, ('multiple_required_backtest_primitives_missing',)
    return DECISION_IMPLEMENT_SNAPSHOT, ('core_evidence_available_and_next_step_is_snapshot_engine',)


def write_holdout_backtest_feasibility_outputs(*, result: HoldoutBacktestFeasibilityResult, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'holdout_backtest_feasibility_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'holdout_backtest_feasibility_summary.md').write_text(_render_markdown(result), encoding='utf-8')
    _write_csv(path / 'proposed_backtest_protocol.csv', result.proposed_protocol_rows)
    _write_csv(path / 'available_evidence_inventory.csv', result.evidence_inventory_rows)
    _write_csv(path / 'required_engine_gaps.csv', result.engine_gap_rows)
    _write_csv(path / 'leakage_risk_register.csv', result.leakage_risk_rows)
    _write_csv(
        path / 'decision.csv',
        [
            {
                'decision_recommendation': result.decision_recommendation,
                'decision_reasons': '; '.join(result.decision_reasons),
            }
        ],
    )


def _evidence_row(evidence_id: str, path: Path) -> dict[str, object]:
    return {
        'evidence_id': evidence_id,
        'path': str(path),
        'available': path.exists(),
        'evidence_type': path.suffix.lstrip('.') or 'file',
        'notes': 'available local evidence' if path.exists() else 'missing local evidence',
    }


def _code_row(evidence_id: str, path: Path) -> dict[str, object]:
    row = _evidence_row(evidence_id, path)
    row['evidence_type'] = 'code'
    return row


def _evidence_from_baseline_methods(evidence_id: str, path: Path, method: str) -> dict[str, object]:
    row = _evidence_row(evidence_id, path)
    row['method'] = method
    row['available'] = bool(row['available']) and _csv_contains_method(path, method)
    row['notes'] = f'method `{method}` found in Phase 1 top-20 evidence' if row['available'] else f'method `{method}` not found in local Phase 1 top-20 evidence'
    return row


def _backtest_engine_row(project_root: Path) -> dict[str, object]:
    available = _has_backtest_engine(project_root)
    return {
        'evidence_id': 'backtest_engine_already_present',
        'path': 'src/tradetool',
        'available': available,
        'evidence_type': 'code_search',
        'notes': 'backtest-like implementation found' if available else 'no reusable backtest engine found',
    }


def _csv_contains_method(path: Path, method: str) -> bool:
    if not path.exists():
        return False
    with path.open('r', encoding='utf-8', newline='') as handle:
        return any(row.get('method') == method for row in csv.DictReader(handle))


def _has_backtest_engine(project_root: Path) -> bool:
    ignored = {'holdout_backtest_feasibility.py', 'holdout_backtest_feasibility_cli.py'}
    for path in (project_root / 'src' / 'tradetool').rglob('*.py'):
        if path.name in ignored:
            continue
        lowered_name = path.name.lower()
        if 'backtest' in lowered_name or 'holdout' in lowered_name:
            return True
    return False


def _render_markdown(result: HoldoutBacktestFeasibilityResult) -> str:
    missing_evidence = [row['evidence_id'] for row in result.evidence_inventory_rows if not row['available']]
    gap_summary = {row['capability']: row['status'] for row in result.engine_gap_rows}
    lines = [
        '# Holdout Backtest Feasibility',
        '',
        '## Executive Summary',
        f"- Decision recommendation: `{result.decision_recommendation}`",
        f"- Universe: `{result.universe_id}`",
        f"- Benchmark: `{result.benchmark_ticker}`",
        f"- Data source: `{result.validated_data_source}`",
        f"- Close input source: `{result.validated_close_input_source}`",
        f"- Ranking engine: `{result.ranking_engine_id}`",
        f"- Trade policy engine: `{result.trade_policy_engine_id}`",
        f"- Candidate type engine: `{result.candidate_type_engine_id}`",
        '',
        '## Latest Phase 4x Evidence',
        f"- Stocks: {result.latest_phase4x_summary['stock_ticker_count']}",
        f"- Fetched: {result.latest_phase4x_summary['fetched_ticker_count']}",
        f"- Ranked: {result.latest_phase4x_summary['ranked_count']}",
        f"- BUY: {result.latest_phase4x_summary['buy_count']}",
        f"- WATCH: {result.latest_phase4x_summary['watch_count']}",
        f"- Serious BUY/WATCH red flags: {result.latest_phase4x_summary['serious_buy_watch_red_flag_count']}",
        '',
        '## Evidence Inventory',
        f"- Missing evidence: `{missing_evidence}`",
        '',
        '## Proposed Protocol',
        '- Monthly rebalances on month-end or first trading day after month-end.',
        '- Selection buckets: raw top 10, raw top 20, BUY-only, BUY+WATCH, REVIEW, AVOID.',
        '- Forward windows: 20 and 60 trading days.',
        '- Metrics: absolute, benchmark, excess return, hit rate, spread, median, drawdown proxy, turnover, missing entries/exits.',
        '- Costs: report gross and net using 10 bps one-way cost per position change.',
        '',
        '## Leakage Controls',
        *[f"- `{row['risk_id']}`: {row['control']}" for row in result.leakage_risk_rows],
        '',
        '## Engine Gaps',
        f"- Gap summary: `{gap_summary}`",
        '',
        '## Decision Reasons',
        *[f'- `{reason}`' for reason in result.decision_reasons],
    ]
    return '\n'.join(lines) + '\n'


def _write_csv(path: Path, rows: tuple[dict[str, object], ...] | list[dict[str, object]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames or ['empty'])
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key, '')) for key in fieldnames})


def _csv_value(value: object) -> object:
    if isinstance(value, (tuple, list)):
        return '; '.join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return '' if value is None else value

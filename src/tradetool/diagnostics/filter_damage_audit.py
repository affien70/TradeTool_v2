from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median

from tradetool.diagnostics.challenger_comparison import CHALLENGER_ID, select_challenger_rs6m_trend_risk
from tradetool.diagnostics.holdout_forward_returns import ForwardReturnResult, build_holdout_forward_returns
from tradetool.diagnostics.incumbent_baseline import BASELINE_ID, select_incumbent_baseline
from tradetool.diagnostics.monthly_holdout_backtest import ROUND_TRIP_COST_RATE, generate_monthly_rebalance_dates
from tradetool.policy.trade_policy import MAX_WATCH_DISTANCE_TO_SMA200, MIN_ACCEPTABLE_DRAWDOWN, MIN_ACCEPTABLE_TRADED_VALUE

DECISION_REMOVE_FILTERS = 'remove_damaging_filters_in_next_challenger'
DECISION_KEEP_INCUMBENT = 'keep_incumbent_no_filtered_challenger'
DECISION_INVESTIGATE_SORTING = 'investigate_sorting_not_filters'
DECISION_FIX_METHODOLOGY = 'fix_filter_damage_methodology'
EXPECTED_REPORT_FILES = (
    'filter_damage_summary.md',
    'filter_damage_summary.json',
    'filter_damage_by_gate.csv',
    'filter_damage_removed_picks.csv',
    'filter_damage_monthly.csv',
    'filter_damage_decision.csv',
)
GATES = (
    'above_sma200',
    'return_6m_positive',
    'relative_strength_6m_positive',
    'liquidity',
    'drawdown',
    'sma200_stretch',
)


@dataclass(frozen=True, slots=True)
class FilterDamageAuditResult:
    universe_id: str
    universe_source: str | None
    benchmark_ticker: str
    rebalance_start_date: str
    rebalance_end_date: str
    requested_rebalance_dates: tuple[str, ...]
    data_source: str
    close_input_source: str
    forward_windows: tuple[int, ...]
    transaction_cost_round_trip: float
    incumbent_id: str
    challenger_id: str
    technical_validity: dict[str, object]
    removed_summary: dict[str, object]
    filter_vs_sorting_diagnosis: str
    decision_recommendation: str
    decision_reasons: tuple[str, ...]
    by_gate_rows: tuple[dict[str, object], ...]
    removed_pick_rows: tuple[dict[str, object], ...]
    monthly_rows: tuple[dict[str, object], ...]
    decision_rows: tuple[dict[str, object], ...]
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'universe_id': self.universe_id,
            'universe_source': self.universe_source,
            'benchmark_ticker': self.benchmark_ticker,
            'rebalance_start_date': self.rebalance_start_date,
            'rebalance_end_date': self.rebalance_end_date,
            'requested_rebalance_dates': list(self.requested_rebalance_dates),
            'data_source': self.data_source,
            'close_input_source': self.close_input_source,
            'forward_windows': list(self.forward_windows),
            'transaction_cost_round_trip': self.transaction_cost_round_trip,
            'incumbent_id': self.incumbent_id,
            'challenger_id': self.challenger_id,
            'audited_gates': list(GATES),
            'technical_validity': self.technical_validity,
            'removed_summary': self.removed_summary,
            'filter_vs_sorting_diagnosis': self.filter_vs_sorting_diagnosis,
            'decision_recommendation': self.decision_recommendation,
            'decision_reasons': list(self.decision_reasons),
            'output_files': list(EXPECTED_REPORT_FILES),
            'leakage_controls': {
                'same_rebalance_dates_for_incumbent_and_challenger': True,
                'same_forward_windows_for_incumbent_and_challenger': True,
                'same_benchmark_for_incumbent_and_challenger': True,
                'same_transaction_cost_for_incumbent_and_challenger': True,
                'feature_rows_capped_at_each_rebalance_date': True,
                'forward_returns_used_to_change_selection': False,
                'tuning_applied': False,
                'production_ranking_changed': False,
                'screener_ui_changed': False,
                'ml_score_calculated': False,
                'holdings_adjustment_applied': False,
            },
            'generated_at_utc': self.generated_at_utc,
        }


@dataclass(frozen=True, slots=True)
class _SnapshotContext:
    result: ForwardReturnResult
    rows: tuple[dict[str, object], ...]
    incumbent: tuple[dict[str, object], ...]
    challenger: tuple[dict[str, object], ...]


def build_filter_damage_audit(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str,
    rebalance_start_date: date,
    rebalance_end_date: date,
    data_source: str,
    windows: tuple[int, ...] = (20, 60),
    universe_db_path: str | Path | None = None,
    forward_return_builder=build_holdout_forward_returns,
) -> FilterDamageAuditResult:
    rebalance_dates = generate_monthly_rebalance_dates(rebalance_start_date, rebalance_end_date)
    normalized_windows = tuple(sorted({int(window) for window in windows}))
    snapshots = tuple(
        _build_snapshot(
            forward_return_builder=forward_return_builder,
            db_path=db_path,
            universe_id=universe_id,
            benchmark_ticker=benchmark_ticker,
            rebalance_date=rebalance_date,
            data_source=data_source,
            windows=normalized_windows,
            universe_db_path=universe_db_path,
        )
        for rebalance_date in rebalance_dates
    )
    incumbent_rows = tuple(row for snapshot in snapshots for row in _incumbent_audit_rows(snapshot, windows=normalized_windows))
    monthly_rows = _monthly_rows(incumbent_rows, windows=normalized_windows)
    by_gate_rows = _by_gate_rows(incumbent_rows, windows=normalized_windows)
    technical = _technical_validity(snapshots, incumbent_rows=incumbent_rows, rebalance_dates=rebalance_dates, windows=normalized_windows)
    removed_summary = _removed_summary(incumbent_rows, monthly_rows, windows=normalized_windows)
    diagnosis = _filter_vs_sorting_diagnosis(removed_summary)
    decision, reasons = recommend_next_action(technical=technical, removed_summary=removed_summary, diagnosis=diagnosis)
    decision_rows = _decision_rows(decision=decision, reasons=reasons, removed_summary=removed_summary, diagnosis=diagnosis)
    return FilterDamageAuditResult(
        universe_id=universe_id,
        universe_source=snapshots[0].result.universe_source if snapshots else None,
        benchmark_ticker=benchmark_ticker.strip().upper(),
        rebalance_start_date=rebalance_start_date.isoformat(),
        rebalance_end_date=rebalance_end_date.isoformat(),
        requested_rebalance_dates=tuple(day.isoformat() for day in rebalance_dates),
        data_source=data_source,
        close_input_source='adjusted_close',
        forward_windows=normalized_windows,
        transaction_cost_round_trip=ROUND_TRIP_COST_RATE,
        incumbent_id=BASELINE_ID,
        challenger_id=CHALLENGER_ID,
        technical_validity=technical,
        removed_summary=removed_summary,
        filter_vs_sorting_diagnosis=diagnosis,
        decision_recommendation=decision,
        decision_reasons=reasons,
        by_gate_rows=by_gate_rows,
        removed_pick_rows=incumbent_rows,
        monthly_rows=monthly_rows,
        decision_rows=decision_rows,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_filter_damage_audit_outputs(*, result: FilterDamageAuditResult, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'filter_damage_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'filter_damage_summary.md').write_text(_render_markdown(result), encoding='utf-8')
    _write_csv(path / 'filter_damage_by_gate.csv', result.by_gate_rows)
    _write_csv(path / 'filter_damage_removed_picks.csv', result.removed_pick_rows)
    _write_csv(path / 'filter_damage_monthly.csv', result.monthly_rows)
    _write_csv(path / 'filter_damage_decision.csv', result.decision_rows)


def recommend_next_action(
    *,
    technical: dict[str, object],
    removed_summary: dict[str, object],
    diagnosis: str,
) -> tuple[str, tuple[str, ...]]:
    if not technical['technical_valid'] or not technical['methodology_clean']:
        return DECISION_FIX_METHODOLOGY, ('filter_damage_audit_methodology_or_data_invalid',)
    if diagnosis == 'filters_removed_high_performing_incumbent_winners':
        return DECISION_REMOVE_FILTERS, ('removed_incumbent_winners_explain_challenger_filter_damage',)
    if diagnosis == 'sorting_not_filters':
        return DECISION_INVESTIGATE_SORTING, ('filters_did_not_remove_future_winners',)
    return DECISION_KEEP_INCUMBENT, ('filtered_challenger_has_no_promising_filter_revision_from_this_audit',)


def _build_snapshot(
    *,
    forward_return_builder,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str,
    rebalance_date: date,
    data_source: str,
    windows: tuple[int, ...],
    universe_db_path: str | Path | None,
) -> _SnapshotContext:
    kwargs = {
        'db_path': db_path,
        'universe_id': universe_id,
        'benchmark_ticker': benchmark_ticker,
        'as_of_date': rebalance_date,
        'data_source': data_source,
        'windows': windows,
    }
    if universe_db_path is not None:
        kwargs['universe_db_path'] = universe_db_path
    result = forward_return_builder(**kwargs)
    features_by_ticker = {str(row['ticker']): row for row in result.snapshot.ranked_rows}
    rows = tuple(
        _enriched_pick_row(
            rebalance_date=result.as_of_date,
            candidate=row,
            features=features_by_ticker.get(str(row['ticker']), {}),
            windows=windows,
        )
        for row in result.candidate_rows
    )
    return _SnapshotContext(
        result=result,
        rows=rows,
        incumbent=select_incumbent_baseline(rows),
        challenger=select_challenger_rs6m_trend_risk(rows),
    )


def _enriched_pick_row(
    *,
    rebalance_date: str,
    candidate: dict[str, object],
    features: dict[str, object],
    windows: tuple[int, ...],
) -> dict[str, object]:
    output = {
        'rebalance_date': rebalance_date,
        **candidate,
        'return_6m': features.get('return_6m', candidate.get('return_6m')),
        'relative_strength_6m': features.get('relative_strength_6m', candidate.get('relative_strength_6m')),
        'relative_strength_3m': features.get('relative_strength_3m', candidate.get('relative_strength_3m')),
        'above_sma200': features.get('above_sma200', candidate.get('above_sma200')),
        'drawdown_252': features.get('drawdown_252', candidate.get('drawdown_252')),
        'distance_to_sma200': features.get('distance_to_sma200', candidate.get('distance_to_sma200')),
        'average_traded_value_20': features.get('average_traded_value_20', candidate.get('average_traded_value_20')),
    }
    for window in windows:
        if output.get(f'{window}d_complete'):
            output[f'{window}d_net_return'] = _as_float(output[f'{window}d_return']) - ROUND_TRIP_COST_RATE
            output[f'{window}d_net_excess_return'] = _as_float(output[f'{window}d_excess_return']) - ROUND_TRIP_COST_RATE
        else:
            output[f'{window}d_net_return'] = None
            output[f'{window}d_net_excess_return'] = None
    return output


def _incumbent_audit_rows(snapshot: _SnapshotContext, *, windows: tuple[int, ...]) -> tuple[dict[str, object], ...]:
    challenger_tickers = {str(row['ticker']) for row in snapshot.challenger}
    output = []
    for incumbent_rank, row in enumerate(snapshot.incumbent, start=1):
        failing = _failing_gates(row)
        audit = {
            'rebalance_date': snapshot.result.as_of_date,
            'ticker': row['ticker'],
            'incumbent_rank': incumbent_rank,
            'challenger_included': str(row['ticker']) in challenger_tickers,
            'removed_by_challenger_filters': str(row['ticker']) not in challenger_tickers,
            'failing_gates': ';'.join(failing),
            'failing_gate_count': len(failing),
            'raw_rank': row.get('raw_rank'),
            'raw_score': row.get('raw_score'),
            'trade_signal': row.get('trade_signal'),
            'candidate_type': row.get('candidate_type'),
            'above_sma200': row.get('above_sma200'),
            'return_6m': row.get('return_6m'),
            'relative_strength_6m': row.get('relative_strength_6m'),
            'relative_strength_3m': row.get('relative_strength_3m'),
            'average_traded_value_20': row.get('average_traded_value_20'),
            'drawdown_252': row.get('drawdown_252'),
            'distance_to_sma200': row.get('distance_to_sma200'),
        }
        for window in windows:
            audit[f'{window}d_excess_return'] = row.get(f'{window}d_excess_return')
            audit[f'{window}d_net_excess_return'] = row.get(f'{window}d_net_excess_return')
            audit[f'{window}d_winner'] = _as_optional_float(row.get(f'{window}d_net_excess_return')) is not None and _as_float(row[f'{window}d_net_excess_return']) > 0.0
        output.append(audit)
    return tuple(output)


def _failing_gates(row: dict[str, object]) -> tuple[str, ...]:
    failures: list[str] = []
    if not bool(row.get('above_sma200')):
        failures.append('above_sma200')
    if _as_optional_float(row.get('return_6m')) is None or _as_float(row['return_6m']) <= 0.0:
        failures.append('return_6m_positive')
    if _as_optional_float(row.get('relative_strength_6m')) is None or _as_float(row['relative_strength_6m']) <= 0.0:
        failures.append('relative_strength_6m_positive')
    liquidity = _as_optional_float(row.get('average_traded_value_20'))
    if liquidity is not None and liquidity < MIN_ACCEPTABLE_TRADED_VALUE:
        failures.append('liquidity')
    drawdown = _as_optional_float(row.get('drawdown_252'))
    if drawdown is not None and drawdown < MIN_ACCEPTABLE_DRAWDOWN:
        failures.append('drawdown')
    stretch = _as_optional_float(row.get('distance_to_sma200'))
    if stretch is not None and stretch > MAX_WATCH_DISTANCE_TO_SMA200:
        failures.append('sma200_stretch')
    return tuple(failures)


def _monthly_rows(rows: tuple[dict[str, object], ...], *, windows: tuple[int, ...]) -> tuple[dict[str, object], ...]:
    output = []
    for rebalance_date in sorted({str(row['rebalance_date']) for row in rows}):
        month_rows = tuple(row for row in rows if row['rebalance_date'] == rebalance_date)
        removed = tuple(row for row in month_rows if row['removed_by_challenger_filters'])
        included = tuple(row for row in month_rows if row['challenger_included'])
        row: dict[str, object] = {
            'rebalance_date': rebalance_date,
            'incumbent_pick_count': len(month_rows),
            'removed_incumbent_pick_count': len(removed),
            'challenger_included_incumbent_pick_count': len(included),
        }
        for window in windows:
            row[f'{window}d_removed_winner_count'] = sum(1 for item in removed if item.get(f'{window}d_winner'))
            row[f'{window}d_removed_loser_count'] = sum(1 for item in removed if not item.get(f'{window}d_winner'))
            row[f'{window}d_removed_mean_net_excess_return'] = _mean_net_excess(removed, window)
            row[f'{window}d_included_mean_net_excess_return'] = _mean_net_excess(included, window)
        output.append(row)
    return tuple(output)


def _by_gate_rows(rows: tuple[dict[str, object], ...], *, windows: tuple[int, ...]) -> tuple[dict[str, object], ...]:
    removed = tuple(row for row in rows if row['removed_by_challenger_filters'])
    output = []
    for gate in GATES:
        gate_rows = tuple(row for row in removed if gate in str(row.get('failing_gates', '')).split(';'))
        row: dict[str, object] = {
            'row_type': 'gate',
            'gate': gate,
            'removed_pick_count': len(gate_rows),
            'co_failure_counts': _co_failure_counts(gate_rows, gate),
        }
        for window in windows:
            row[f'{window}d_removed_winner_count'] = sum(1 for item in gate_rows if item.get(f'{window}d_winner'))
            row[f'{window}d_removed_loser_count'] = sum(1 for item in gate_rows if not item.get(f'{window}d_winner'))
            row[f'{window}d_mean_net_excess_return'] = _mean_net_excess(gate_rows, window)
            row[f'{window}d_median_net_excess_return'] = _median_net_excess(gate_rows, window)
        output.append(row)
    for first_index, first in enumerate(GATES):
        for second in GATES[first_index + 1:]:
            pair_rows = tuple(
                row for row in removed
                if first in str(row.get('failing_gates', '')).split(';') and second in str(row.get('failing_gates', '')).split(';')
            )
            if not pair_rows:
                continue
            row = {'row_type': 'gate_pair', 'gate': f'{first}+{second}', 'removed_pick_count': len(pair_rows), 'co_failure_counts': ''}
            for window in windows:
                row[f'{window}d_removed_winner_count'] = sum(1 for item in pair_rows if item.get(f'{window}d_winner'))
                row[f'{window}d_removed_loser_count'] = sum(1 for item in pair_rows if not item.get(f'{window}d_winner'))
                row[f'{window}d_mean_net_excess_return'] = _mean_net_excess(pair_rows, window)
                row[f'{window}d_median_net_excess_return'] = _median_net_excess(pair_rows, window)
            output.append(row)
    return tuple(output)


def _removed_summary(rows: tuple[dict[str, object], ...], monthly_rows: tuple[dict[str, object], ...], *, windows: tuple[int, ...]) -> dict[str, object]:
    removed = tuple(row for row in rows if row['removed_by_challenger_filters'])
    included = tuple(row for row in rows if row['challenger_included'])
    summary: dict[str, object] = {
        'incumbent_pick_count': len(rows),
        'removed_incumbent_pick_count': len(removed),
        'challenger_included_incumbent_pick_count': len(included),
    }
    for window in windows:
        summary[f'{window}d_removed_winner_count'] = sum(1 for row in removed if row.get(f'{window}d_winner'))
        summary[f'{window}d_removed_loser_count'] = sum(1 for row in removed if not row.get(f'{window}d_winner'))
        summary[f'{window}d_removed_mean_net_excess_return'] = _mean_net_excess(removed, window)
        summary[f'{window}d_included_mean_net_excess_return'] = _mean_net_excess(included, window)
    by_gate_winners = {}
    for gate in GATES:
        gate_rows = tuple(row for row in removed if gate in str(row.get('failing_gates', '')).split(';'))
        by_gate_winners[gate] = sum(1 for row in gate_rows if row.get('60d_winner'))
    summary['gate_removing_most_60d_winners'] = max(by_gate_winners.items(), key=lambda item: (item[1], item[0]))[0] if by_gate_winners else None
    summary['gate_60d_removed_winner_counts'] = by_gate_winners
    summary['months_with_removed_winners_60d'] = sum(1 for row in monthly_rows if int(row.get('60d_removed_winner_count', 0)) > 0)
    return summary


def _filter_vs_sorting_diagnosis(summary: dict[str, object]) -> str:
    removed_winners = int(summary.get('60d_removed_winner_count', 0))
    removed_mean = _as_optional_float(summary.get('60d_removed_mean_net_excess_return'))
    included_mean = _as_optional_float(summary.get('60d_included_mean_net_excess_return'))
    if removed_winners <= 0:
        return 'sorting_not_filters'
    if removed_mean is not None and removed_mean > 0.0:
        return 'filters_removed_high_performing_incumbent_winners'
    if included_mean is not None and removed_mean is not None and removed_mean > included_mean:
        return 'filters_removed_positive_incumbent_winners'
    return 'filters_removed_low_quality_incumbent_picks'


def _technical_validity(
    snapshots: tuple[_SnapshotContext, ...],
    *,
    incumbent_rows: tuple[dict[str, object], ...],
    rebalance_dates: tuple[date, ...],
    windows: tuple[int, ...],
) -> dict[str, object]:
    ranked_counts = [snapshot.result.snapshot.ranked_count for snapshot in snapshots]
    missing_exits = sum(len(snapshot.result.missing_exit_rows) for snapshot in snapshots)
    return {
        'technical_valid': bool(snapshots) and all(snapshot.result.snapshot.snapshot_valid for snapshot in snapshots) and missing_exits == 0,
        'methodology_clean': True,
        'rebalance_date_count': len(rebalance_dates),
        'requested_rebalance_dates': [day.isoformat() for day in rebalance_dates],
        'valid_snapshot_count': sum(1 for snapshot in snapshots if snapshot.result.snapshot.snapshot_valid),
        'ranked_count_total': sum(ranked_counts),
        'ranked_count_min': min(ranked_counts) if ranked_counts else 0,
        'ranked_count_max': max(ranked_counts) if ranked_counts else 0,
        'incumbent_pick_count': len(incumbent_rows),
        'missing_forward_exit_count': missing_exits,
        'complete_return_counts': {
            str(window): sum(1 for snapshot in snapshots for row in snapshot.rows if row.get(f'{window}d_complete'))
            for window in windows
        },
    }


def _decision_rows(
    *,
    decision: str,
    reasons: tuple[str, ...],
    removed_summary: dict[str, object],
    diagnosis: str,
) -> tuple[dict[str, object], ...]:
    return (
        {
            'decision_recommendation': decision,
            'decision_reasons': '; '.join(reasons),
            'filter_vs_sorting_diagnosis': diagnosis,
            'removed_incumbent_pick_count': removed_summary.get('removed_incumbent_pick_count'),
            '60d_removed_winner_count': removed_summary.get('60d_removed_winner_count'),
            '60d_removed_mean_net_excess_return': removed_summary.get('60d_removed_mean_net_excess_return'),
            'gate_removing_most_60d_winners': removed_summary.get('gate_removing_most_60d_winners'),
        },
    )


def _co_failure_counts(rows: tuple[dict[str, object], ...], gate: str) -> str:
    counts = []
    for other in GATES:
        if other == gate:
            continue
        count = sum(1 for row in rows if other in str(row.get('failing_gates', '')).split(';'))
        if count:
            counts.append(f'{other}={count}')
    return ';'.join(counts)


def _mean_net_excess(rows: tuple[dict[str, object], ...], window: int) -> float | None:
    values = [_as_float(row[f'{window}d_net_excess_return']) for row in rows if _as_optional_float(row.get(f'{window}d_net_excess_return')) is not None]
    return _mean(values)


def _median_net_excess(rows: tuple[dict[str, object], ...], window: int) -> float | None:
    values = [_as_float(row[f'{window}d_net_excess_return']) for row in rows if _as_optional_float(row.get(f'{window}d_net_excess_return')) is not None]
    return None if not values else median(values)


def _render_markdown(result: FilterDamageAuditResult) -> str:
    lines = [
        '# Filter Damage Audit',
        '',
        f'- Decision recommendation: `{result.decision_recommendation}`',
        f'- Diagnosis: `{result.filter_vs_sorting_diagnosis}`',
        f'- Incumbent: `{result.incumbent_id}`',
        f'- Challenger: `{result.challenger_id}`',
        f'- Valid snapshots: {result.technical_validity["valid_snapshot_count"]}/{result.technical_validity["rebalance_date_count"]}',
        f'- Ranked count total/range: {result.technical_validity["ranked_count_total"]} / {result.technical_validity["ranked_count_min"]}-{result.technical_validity["ranked_count_max"]}',
        f'- Removed incumbent picks: {result.removed_summary["removed_incumbent_pick_count"]}/{result.removed_summary["incumbent_pick_count"]}',
        f'- 60d removed winners: {result.removed_summary.get("60d_removed_winner_count")}',
        f'- 60d removed mean net excess: `{result.removed_summary.get("60d_removed_mean_net_excess_return")}`',
        f'- Gate removing most 60d winners: `{result.removed_summary.get("gate_removing_most_60d_winners")}`',
        '',
        '## Decision Reasons',
    ]
    lines.extend(f'- `{reason}`' for reason in result.decision_reasons)
    lines.extend(
        [
            '',
            '## Guardrails',
            '- Research-only audit; no tuning, production ranking, Screener UI, ML, Holdings, or production DB write.',
        ]
    )
    return '\n'.join(lines) + '\n'


def _write_csv(path: Path, rows) -> None:
    fieldnames: list[str] = []
    normalized_rows = []
    for source in rows:
        row = {key: _csv_value(value) for key, value in source.items()}
        normalized_rows.append(row)
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = ['empty']
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in normalized_rows:
            writer.writerow(row)


def _csv_value(value: object) -> object:
    if isinstance(value, (list, tuple, set)):
        return ';'.join(str(item) for item in value)
    return value


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


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

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import median

from tradetool.diagnostics.holdout_forward_returns import (
    DECISION_ALIGNMENT,
    DECISION_BLOCKED_HISTORY,
    DECISION_DATA_GAPS,
    DECISION_PERFORMANCE,
    ForwardReturnResult,
    build_holdout_forward_returns,
)

DECISION_CONTINUE_REVIEW = 'continue_to_monthly_holdout_review'
DECISION_INVESTIGATE_UNDERPERFORMANCE = 'investigate_holdout_underperformance'
DECISION_INVESTIGATE_GAPS = 'investigate_data_gaps_before_backtest'
DECISION_FIX_ALIGNMENT = 'fix_monthly_rebalance_alignment'
DECISION_BLOCKED_FORWARD_HISTORY = 'blocked_insufficient_forward_history'
ROUND_TRIP_COST_RATE = 0.002
EXPECTED_REPORT_FILES = (
    'monthly_candidate_type_summary.csv',
    'monthly_group_summary.csv',
    'monthly_holdout_decision.csv',
    'monthly_holdout_summary.json',
    'monthly_holdout_summary.md',
    'monthly_missing_exits.csv',
    'monthly_pick_returns.csv',
    'monthly_rebalance_summary.csv',
    'monthly_signal_summary.csv',
    'monthly_topn_summary.csv',
)


@dataclass(frozen=True, slots=True)
class MonthlyHoldoutBacktestResult:
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
    snapshot_count: int
    valid_snapshot_count: int
    total_ranked_count: int
    monthly_rebalance_rows: tuple[dict[str, object], ...]
    pick_rows: tuple[dict[str, object], ...]
    monthly_group_summary_rows: tuple[dict[str, object], ...]
    topn_summary_rows: tuple[dict[str, object], ...]
    signal_summary_rows: tuple[dict[str, object], ...]
    candidate_type_summary_rows: tuple[dict[str, object], ...]
    missing_exit_rows: tuple[dict[str, object], ...]
    decision_recommendation: str
    decision_reasons: tuple[str, ...]
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'universe_id': self.universe_id,
            'universe_source': self.universe_source,
            'benchmark_ticker': self.benchmark_ticker,
            'rebalance_start_date': self.rebalance_start_date,
            'rebalance_end_date': self.rebalance_end_date,
            'rebalance_rule': (
                'calendar month-end dates between start and end, capped to the requested end date; '
                'each snapshot uses the latest available feature rows at or before the requested rebalance date'
            ),
            'requested_rebalance_dates': list(self.requested_rebalance_dates),
            'data_source': self.data_source,
            'close_input_source': self.close_input_source,
            'forward_windows': list(self.forward_windows),
            'transaction_cost_round_trip': self.transaction_cost_round_trip,
            'snapshot_count': self.snapshot_count,
            'valid_snapshot_count': self.valid_snapshot_count,
            'total_ranked_count': self.total_ranked_count,
            'complete_return_counts': {
                str(window): sum(1 for row in self.pick_rows if row.get(f'{window}d_complete'))
                for window in self.forward_windows
            },
            'missing_forward_exit_count': len(self.missing_exit_rows),
            'decision_recommendation': self.decision_recommendation,
            'decision_reasons': list(self.decision_reasons),
            'leakage_controls': {
                'feature_rows_capped_at_each_rebalance_date': True,
                'benchmark_feature_rows_capped_at_each_rebalance_date': True,
                'forward_returns_use_rows_strictly_after_rebalance_date': True,
                'forward_returns_used_to_change_rank_signal_type': False,
                'ml_score_calculated': False,
                'holdings_adjustment_applied': False,
                'manual_focus_boost_applied': False,
                'current_candidate_list_used_as_target': False,
            },
            'generated_at_utc': self.generated_at_utc,
        }


def build_monthly_holdout_backtest(
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
) -> MonthlyHoldoutBacktestResult:
    rebalance_dates = generate_monthly_rebalance_dates(rebalance_start_date, rebalance_end_date)
    normalized_windows = tuple(sorted({int(window) for window in windows}))
    snapshot_results: list[ForwardReturnResult] = []
    for rebalance_date in rebalance_dates:
        kwargs = {
            'db_path': db_path,
            'universe_id': universe_id,
            'benchmark_ticker': benchmark_ticker,
            'as_of_date': rebalance_date,
            'data_source': data_source,
            'windows': normalized_windows,
        }
        if universe_db_path is not None:
            kwargs['universe_db_path'] = universe_db_path
        snapshot_results.append(forward_return_builder(**kwargs))

    pick_rows = tuple(
        _monthly_pick_row(snapshot=result, row=row)
        for result in snapshot_results
        for row in result.candidate_rows
    )
    monthly_rebalance_rows = tuple(_rebalance_summary_row(result, windows=normalized_windows) for result in snapshot_results)
    monthly_group_rows = tuple(
        row
        for result in snapshot_results
        for row in _aggregate_standard_groups(
            rows=tuple(_monthly_pick_row(snapshot=result, row=candidate) for candidate in result.candidate_rows),
            windows=normalized_windows,
            rebalance_date=result.as_of_date,
        )
    )
    topn_rows = _aggregate_topn(rows=pick_rows, windows=normalized_windows)
    signal_rows = _aggregate_field(rows=pick_rows, field='trade_signal', windows=normalized_windows)
    candidate_type_rows = _aggregate_field(rows=pick_rows, field='candidate_type', windows=normalized_windows)
    missing_rows = tuple(
        _with_rebalance_date(result.as_of_date, row)
        for result in snapshot_results
        for row in result.missing_exit_rows
    )
    decision, reasons = recommend_monthly_next_action(
        snapshot_results=tuple(snapshot_results),
        pick_rows=pick_rows,
        topn_summary_rows=topn_rows,
        windows=normalized_windows,
    )
    return MonthlyHoldoutBacktestResult(
        universe_id=universe_id,
        universe_source=snapshot_results[0].universe_source if snapshot_results else None,
        benchmark_ticker=benchmark_ticker.strip().upper(),
        rebalance_start_date=rebalance_start_date.isoformat(),
        rebalance_end_date=rebalance_end_date.isoformat(),
        requested_rebalance_dates=tuple(day.isoformat() for day in rebalance_dates),
        data_source=data_source,
        close_input_source='adjusted_close',
        forward_windows=normalized_windows,
        transaction_cost_round_trip=ROUND_TRIP_COST_RATE,
        snapshot_count=len(snapshot_results),
        valid_snapshot_count=sum(1 for result in snapshot_results if result.snapshot.snapshot_valid),
        total_ranked_count=len(pick_rows),
        monthly_rebalance_rows=monthly_rebalance_rows,
        pick_rows=pick_rows,
        monthly_group_summary_rows=monthly_group_rows,
        topn_summary_rows=topn_rows,
        signal_summary_rows=signal_rows,
        candidate_type_summary_rows=candidate_type_rows,
        missing_exit_rows=missing_rows,
        decision_recommendation=decision,
        decision_reasons=reasons,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def generate_monthly_rebalance_dates(start_date: date, end_date: date) -> tuple[date, ...]:
    if end_date < start_date:
        raise ValueError('rebalance_end_date must be on or after rebalance_start_date')
    dates: list[date] = []
    cursor = date(start_date.year, start_date.month, 1)
    while cursor <= end_date:
        candidate = min(_month_end(cursor), end_date)
        if candidate >= start_date and (not dates or dates[-1] != candidate):
            dates.append(candidate)
        cursor = _next_month(cursor)
    return tuple(dates)


def write_monthly_holdout_outputs(*, result: MonthlyHoldoutBacktestResult, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'monthly_holdout_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'monthly_holdout_summary.md').write_text(_render_markdown(result), encoding='utf-8')
    _write_csv(path / 'monthly_rebalance_summary.csv', result.monthly_rebalance_rows)
    _write_csv(path / 'monthly_pick_returns.csv', result.pick_rows)
    _write_csv(path / 'monthly_group_summary.csv', result.monthly_group_summary_rows)
    _write_csv(path / 'monthly_topn_summary.csv', result.topn_summary_rows)
    _write_csv(path / 'monthly_signal_summary.csv', result.signal_summary_rows)
    _write_csv(path / 'monthly_candidate_type_summary.csv', result.candidate_type_summary_rows)
    _write_csv(path / 'monthly_missing_exits.csv', result.missing_exit_rows)
    _write_csv(
        path / 'monthly_holdout_decision.csv',
        [{'decision_recommendation': result.decision_recommendation, 'decision_reasons': '; '.join(result.decision_reasons)}],
    )


def recommend_monthly_next_action(
    *,
    snapshot_results: tuple[ForwardReturnResult, ...],
    pick_rows: tuple[dict[str, object], ...],
    topn_summary_rows: tuple[dict[str, object], ...],
    windows: tuple[int, ...],
) -> tuple[str, tuple[str, ...]]:
    if len(snapshot_results) < 3:
        return DECISION_BLOCKED_FORWARD_HISTORY, ('fewer_than_three_monthly_snapshots',)
    if not pick_rows:
        return DECISION_BLOCKED_FORWARD_HISTORY, ('no_ranked_candidates_across_monthly_snapshots',)
    if any(result.decision_recommendation == DECISION_ALIGNMENT for result in snapshot_results):
        return DECISION_FIX_ALIGNMENT, ('single_snapshot_alignment_warning_detected',)
    complete_counts = {window: sum(1 for row in pick_rows if row.get(f'{window}d_complete')) for window in windows}
    if any(count == 0 for count in complete_counts.values()):
        return DECISION_BLOCKED_FORWARD_HISTORY, ('not_enough_forward_history_for_requested_windows',)
    missing_count = sum(
        1
        for row in pick_rows
        for window in windows
        if row.get(f'{window}d_missing_reason')
    )
    total_checks = max(1, len(pick_rows) * len(windows))
    if missing_count / total_checks > 0.20 or any(result.decision_recommendation in {DECISION_BLOCKED_HISTORY, DECISION_DATA_GAPS} for result in snapshot_results):
        return DECISION_INVESTIGATE_GAPS, ('monthly_forward_return_gaps_exceed_tolerance',)
    if _top_candidates_underperformed(topn_summary_rows, windows):
        return DECISION_INVESTIGATE_UNDERPERFORMANCE, ('top_candidates_underperformed_materially_across_monthly_panel',)
    return DECISION_CONTINUE_REVIEW, ('monthly_holdout_metrics_available_for_review',)


def _monthly_pick_row(*, snapshot: ForwardReturnResult, row: dict[str, object]) -> dict[str, object]:
    output = {
        'rebalance_date': snapshot.as_of_date,
        'effective_feature_date': snapshot.effective_feature_date,
        **row,
    }
    for window in snapshot.forward_windows:
        gross_return = row.get(f'{window}d_return')
        gross_excess = row.get(f'{window}d_excess_return')
        if row.get(f'{window}d_complete'):
            output[f'{window}d_net_return'] = _as_float(gross_return) - ROUND_TRIP_COST_RATE
            output[f'{window}d_net_excess_return'] = _as_float(gross_excess) - ROUND_TRIP_COST_RATE
        else:
            output[f'{window}d_net_return'] = None
            output[f'{window}d_net_excess_return'] = None
    return output


def _rebalance_summary_row(result: ForwardReturnResult, *, windows: tuple[int, ...]) -> dict[str, object]:
    row: dict[str, object] = {
        'rebalance_date': result.as_of_date,
        'effective_feature_date': result.effective_feature_date,
        'snapshot_valid': result.snapshot.snapshot_valid,
        'ranked_count': result.snapshot.ranked_count,
        'feature_complete_count': result.snapshot.feature_complete_count,
        'feature_incomplete_count': result.snapshot.feature_incomplete_count,
        'missing_forward_exit_count': len(result.missing_exit_rows),
        'single_snapshot_decision': result.decision_recommendation,
    }
    for window in windows:
        row[f'{window}d_complete_count'] = sum(1 for candidate in result.candidate_rows if candidate.get(f'{window}d_complete'))
    return row


def _aggregate_standard_groups(
    *,
    rows: tuple[dict[str, object], ...],
    windows: tuple[int, ...],
    rebalance_date: str | None = None,
) -> tuple[dict[str, object], ...]:
    specs = (
        ('raw_top_5', lambda row: int(row['raw_rank']) <= 5),
        ('raw_top_10', lambda row: int(row['raw_rank']) <= 10),
        ('raw_top_20', lambda row: int(row['raw_rank']) <= 20),
        ('BUY', lambda row: row.get('trade_signal') == 'BUY'),
        ('BUY+WATCH', lambda row: row.get('trade_signal') in {'BUY', 'WATCH'}),
        ('REVIEW', lambda row: row.get('trade_signal') == 'REVIEW'),
        ('AVOID', lambda row: row.get('trade_signal') == 'AVOID'),
    )
    output: list[dict[str, object]] = []
    for name, predicate in specs:
        group_rows = tuple(row for row in rows if predicate(row))
        for window in windows:
            aggregate = _aggregate_rows(group=name, rows=group_rows, window=window)
            if rebalance_date is not None:
                aggregate = {'rebalance_date': rebalance_date, **aggregate}
            output.append(aggregate)
    return tuple(output)


def _aggregate_topn(*, rows: tuple[dict[str, object], ...], windows: tuple[int, ...]) -> tuple[dict[str, object], ...]:
    specs = (('raw_top_5', 5), ('raw_top_10', 10), ('raw_top_20', 20))
    return tuple(
        _aggregate_rows(group=name, rows=tuple(row for row in rows if int(row['raw_rank']) <= limit), window=window)
        for name, limit in specs
        for window in windows
    )


def _aggregate_field(*, rows: tuple[dict[str, object], ...], field: str, windows: tuple[int, ...]) -> tuple[dict[str, object], ...]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[field])].append(row)
    return tuple(
        _aggregate_rows(group=name, rows=tuple(group_rows), window=window)
        for name, group_rows in sorted(grouped.items())
        for window in windows
    )


def _aggregate_rows(*, group: str, rows: tuple[dict[str, object], ...], window: int) -> dict[str, object]:
    complete = tuple(row for row in rows if row.get(f'{window}d_complete'))
    returns = [_as_float(row.get(f'{window}d_return')) for row in complete]
    net_returns = [_as_float(row.get(f'{window}d_net_return')) for row in complete]
    benchmarks = [_as_float(row.get(f'{window}d_benchmark_return')) for row in complete]
    excess = [_as_float(row.get(f'{window}d_excess_return')) for row in complete]
    net_excess = [_as_float(row.get(f'{window}d_net_excess_return')) for row in complete]
    return {
        'group': group,
        'forward_window_trading_days': window,
        'candidate_count': len(rows),
        'complete_count': len(complete),
        'mean_forward_return': _mean(returns),
        'median_forward_return': None if not returns else median(returns),
        'mean_net_return': _mean(net_returns),
        'median_net_return': None if not net_returns else median(net_returns),
        'mean_benchmark_return': _mean(benchmarks),
        'mean_excess_return': _mean(excess),
        'median_excess_return': None if not excess else median(excess),
        'mean_net_excess_return': _mean(net_excess),
        'median_net_excess_return': None if not net_excess else median(net_excess),
        'hit_rate_vs_benchmark': _mean([1.0 if value > 0 else 0.0 for value in excess]),
        'positive_return_rate': _mean([1.0 if value > 0 else 0.0 for value in returns]),
    }


def _top_candidates_underperformed(topn_summary_rows: tuple[dict[str, object], ...], windows: tuple[int, ...]) -> bool:
    top10_rows = {
        int(row['forward_window_trading_days']): row
        for row in topn_summary_rows
        if row.get('group') == 'raw_top_10'
    }
    if not all(window in top10_rows for window in windows):
        return False
    for window in windows:
        mean_net_excess = _as_optional_float(top10_rows[window].get('mean_net_excess_return'))
        hit_rate = _as_optional_float(top10_rows[window].get('hit_rate_vs_benchmark'))
        if mean_net_excess is None or hit_rate is None:
            return False
        if mean_net_excess > -0.10 or hit_rate > 0.20:
            return False
    return True


def _with_rebalance_date(rebalance_date: str, row: dict[str, object]) -> dict[str, object]:
    return {'rebalance_date': rebalance_date, **row}


def _render_markdown(result: MonthlyHoldoutBacktestResult) -> str:
    lines = [
        '# Monthly Holdout Backtest Summary',
        '',
        '## Technical Summary',
        f'- Decision recommendation: `{result.decision_recommendation}`',
        f'- Universe source: `{result.universe_source or "none"}`',
        f'- Rebalance date count: {result.snapshot_count}',
        f'- Rebalance dates: `{list(result.requested_rebalance_dates)}`',
        f'- Valid snapshots: {result.valid_snapshot_count}/{result.snapshot_count}',
        f'- Total ranked rows: {result.total_ranked_count}',
        f'- Benchmark: `{result.benchmark_ticker}`',
        f'- Close input source: `{result.close_input_source}`',
        f'- Forward windows: `{list(result.forward_windows)}`',
        f'- Round-trip transaction cost per pick: {result.transaction_cost_round_trip}',
        '',
        '## Rebalance Rule',
        '- Requested dates are calendar month-end dates between start and end, capped to the requested end date.',
        '- Snapshot feature rows use the latest available ticker and benchmark rows at or before each requested rebalance date.',
        '- Forward return entry rows are strictly after each requested rebalance date.',
        '',
        '## Leakage Controls',
        '- Feature rows and benchmark feature rows are capped at each rebalance date.',
        '- Forward returns did not change rank, signal, or candidate type.',
        '- No ML score, Holdings adjustment, manual focus boost, or current candidate target list was applied.',
        '',
        '## Top-N Aggregate Summary',
    ]
    for row in result.topn_summary_rows:
        lines.append(
            f"- {row['group']} {row['forward_window_trading_days']}d: "
            f"complete={row['complete_count']}/{row['candidate_count']} "
            f"mean_return={row['mean_forward_return']} "
            f"mean_excess={row['mean_excess_return']} "
            f"mean_net_excess={row['mean_net_excess_return']}"
        )
    lines.extend(['', '## Decision Reasons'])
    lines.extend(f'- `{reason}`' for reason in result.decision_reasons)
    return '\n'.join(lines) + '\n'


def _month_end(day: date) -> date:
    return _next_month(day.replace(day=1)) - timedelta(days=1)


def _next_month(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def _write_csv(path: Path, rows) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = ['empty']
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


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
    if value is None:
        return None
    return _as_float(value)

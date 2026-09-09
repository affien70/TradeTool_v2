from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median

from tradetool.diagnostics.holdout_forward_returns import ForwardReturnResult, build_holdout_forward_returns
from tradetool.diagnostics.monthly_holdout_backtest import ROUND_TRIP_COST_RATE, generate_monthly_rebalance_dates

DECISION_CONTINUE_EXTENDED = 'continue_to_extended_holdout_window'
DECISION_V2_UNDERPERFORMANCE = 'investigate_v2_underperformance_vs_naive'
DECISION_NAIVE_DOMINANCE = 'investigate_naive_baseline_dominance'
DECISION_FIX_METHODOLOGY = 'fix_comparison_methodology'
DECISION_BLOCKED_DATA = 'blocked_data_or_alignment_issue'
EXPECTED_REPORT_FILES = (
    'baseline_naive_comparison_summary.json',
    'baseline_naive_comparison_summary.md',
    'baseline_naive_group_summary.csv',
    'baseline_naive_monthly_breakdown.csv',
    'baseline_naive_topn_vs_naive.csv',
    'baseline_naive_decision.csv',
)


@dataclass(frozen=True, slots=True)
class BaselineNaiveComparisonResult:
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
    technical_validity: dict[str, object]
    group_summary_rows: tuple[dict[str, object], ...]
    monthly_breakdown_rows: tuple[dict[str, object], ...]
    topn_vs_naive_rows: tuple[dict[str, object], ...]
    quality_answers: dict[str, object]
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
            'requested_rebalance_dates': list(self.requested_rebalance_dates),
            'data_source': self.data_source,
            'close_input_source': self.close_input_source,
            'forward_windows': list(self.forward_windows),
            'transaction_cost_round_trip': self.transaction_cost_round_trip,
            'technical_validity': self.technical_validity,
            'quality_answers': self.quality_answers,
            'decision_recommendation': self.decision_recommendation,
            'decision_reasons': list(self.decision_reasons),
            'leakage_controls': {
                'same_rebalance_dates_for_all_groups': True,
                'same_forward_windows_for_all_groups': True,
                'same_benchmark_for_all_groups': True,
                'same_adjusted_close_source_for_all_groups': True,
                'forward_returns_used_to_change_rank_signal_type': False,
                'tuning_applied': False,
                'ml_score_calculated': False,
                'holdings_adjustment_applied': False,
                'screener_ui_changed': False,
            },
            'generated_at_utc': self.generated_at_utc,
        }


def build_baseline_naive_comparison(
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
) -> BaselineNaiveComparisonResult:
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
    grouped_rows = tuple(row for snapshot in snapshots for row in _group_rows_for_snapshot(snapshot))
    group_summary = _summarize_grouped_rows(grouped_rows, windows=normalized_windows)
    monthly_breakdown = _summarize_grouped_rows(grouped_rows, windows=normalized_windows, include_month=True)
    topn_vs_naive = _topn_vs_naive_rows(group_summary, windows=normalized_windows)
    technical = _technical_validity(snapshots, rebalance_dates=rebalance_dates, windows=normalized_windows)
    quality = _quality_answers(group_summary, topn_vs_naive, windows=normalized_windows)
    decision, reasons = recommend_next_action(technical=technical, quality_answers=quality, group_summary_rows=group_summary)
    return BaselineNaiveComparisonResult(
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
        technical_validity=technical,
        group_summary_rows=group_summary,
        monthly_breakdown_rows=monthly_breakdown,
        topn_vs_naive_rows=topn_vs_naive,
        quality_answers=quality,
        decision_recommendation=decision,
        decision_reasons=reasons,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_baseline_naive_comparison_outputs(*, result: BaselineNaiveComparisonResult, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'baseline_naive_comparison_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'baseline_naive_comparison_summary.md').write_text(_render_markdown(result), encoding='utf-8')
    _write_csv(path / 'baseline_naive_group_summary.csv', result.group_summary_rows)
    _write_csv(path / 'baseline_naive_monthly_breakdown.csv', result.monthly_breakdown_rows)
    _write_csv(path / 'baseline_naive_topn_vs_naive.csv', result.topn_vs_naive_rows)
    _write_csv(
        path / 'baseline_naive_decision.csv',
        [{'decision_recommendation': result.decision_recommendation, 'decision_reasons': '; '.join(result.decision_reasons)}],
    )


def recommend_next_action(
    *,
    technical: dict[str, object],
    quality_answers: dict[str, object],
    group_summary_rows: tuple[dict[str, object], ...],
) -> tuple[str, tuple[str, ...]]:
    if not technical['technical_valid'] or technical['missing_forward_exit_count']:
        return DECISION_BLOCKED_DATA, ('data_or_alignment_issue_prevents_naive_comparison',)
    if _methodology_suspicious(technical):
        return DECISION_FIX_METHODOLOGY, ('comparison_dates_or_windows_are_not_identical',)
    if quality_answers['one_naive_baseline_dominates_all_v2_groups']:
        return DECISION_NAIVE_DOMINANCE, ('one_naive_baseline_dominates_all_v2_groups',)
    if quality_answers['v2_clearly_underperforms_simple_naive']:
        return DECISION_V2_UNDERPERFORMANCE, ('v2_underperforms_simple_naive_baselines_no_tuning',)
    return DECISION_CONTINUE_EXTENDED, ('v2_competitive_enough_for_extended_holdout_window',)


@dataclass(frozen=True, slots=True)
class _SnapshotContext:
    result: ForwardReturnResult
    rows: tuple[dict[str, object], ...]


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
        _enriched_pick_row(rebalance_date=result.as_of_date, candidate=row, features=features_by_ticker.get(str(row['ticker']), {}), windows=windows)
        for row in result.candidate_rows
    )
    return _SnapshotContext(result=result, rows=rows)


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
        'return_6m': features.get('return_6m'),
        'relative_strength_6m': features.get('relative_strength_6m'),
        'relative_strength_3m': features.get('relative_strength_3m'),
        'above_sma200': features.get('above_sma200'),
    }
    for window in windows:
        if candidate.get(f'{window}d_complete'):
            output[f'{window}d_net_return'] = _as_float(candidate[f'{window}d_return']) - ROUND_TRIP_COST_RATE
            output[f'{window}d_net_excess_return'] = _as_float(candidate[f'{window}d_excess_return']) - ROUND_TRIP_COST_RATE
        else:
            output[f'{window}d_net_return'] = None
            output[f'{window}d_net_excess_return'] = None
    return output


def _group_rows_for_snapshot(snapshot: _SnapshotContext) -> tuple[dict[str, object], ...]:
    specs = (
        ('v2', 'v2_raw_top_5', _raw_top(snapshot.rows, 5)),
        ('v2', 'v2_raw_top_10', _raw_top(snapshot.rows, 10)),
        ('v2', 'v2_raw_top_20', _raw_top(snapshot.rows, 20)),
        ('v2', 'v2_BUY', tuple(row for row in snapshot.rows if row.get('trade_signal') == 'BUY')),
        ('v2', 'v2_BUY+WATCH', tuple(row for row in snapshot.rows if row.get('trade_signal') in {'BUY', 'WATCH'})),
        ('v2', 'v2_REVIEW', tuple(row for row in snapshot.rows if row.get('trade_signal') == 'REVIEW')),
        ('v2', 'v2_AVOID', tuple(row for row in snapshot.rows if row.get('trade_signal') == 'AVOID')),
        ('naive', 'naive_equal_weight_feature_complete', snapshot.rows),
        ('naive', 'naive_return_6m_top_10', _top_by_field(snapshot.rows, 'return_6m', 10)),
        ('naive', 'naive_return_6m_top_20', _top_by_field(snapshot.rows, 'return_6m', 20)),
        ('naive', 'naive_rs_6m_top_10', _top_by_field(snapshot.rows, 'relative_strength_6m', 10)),
        ('naive', 'naive_rs_6m_top_20', _top_by_field(snapshot.rows, 'relative_strength_6m', 20)),
        ('naive', 'naive_rs_3m_top_10', _top_by_field(snapshot.rows, 'relative_strength_3m', 10)),
        ('naive', 'naive_rs_3m_top_20', _top_by_field(snapshot.rows, 'relative_strength_3m', 20)),
        ('naive', 'naive_trend_rs_6m_top_20', _trend_rs_top20(snapshot.rows)),
        ('control', 'v2_bottom_20_raw_rank', _bottom_by_raw_rank(snapshot.rows, 20)),
    )
    return tuple(
        {
            'rebalance_date': snapshot.result.as_of_date,
            'group_category': category,
            'group': group,
            **row,
        }
        for category, group, rows in specs
        for row in rows
    )


def _summarize_grouped_rows(
    grouped_rows: tuple[dict[str, object], ...],
    *,
    windows: tuple[int, ...],
    include_month: bool = False,
) -> tuple[dict[str, object], ...]:
    keys = sorted({(row['rebalance_date'], row['group_category'], row['group']) if include_month else (row['group_category'], row['group']) for row in grouped_rows})
    output = []
    for key in keys:
        if include_month:
            rebalance_date, category, group = key
            rows = tuple(row for row in grouped_rows if row['rebalance_date'] == rebalance_date and row['group'] == group)
        else:
            category, group = key
            rows = tuple(row for row in grouped_rows if row['group'] == group)
        for window in windows:
            aggregate = _aggregate_rows(rows=rows, window=window)
            prefix = {'rebalance_date': key[0]} if include_month else {}
            output.append({**prefix, 'group_category': category, 'group': group, 'forward_window_trading_days': window, **aggregate})
    return tuple(output)


def _aggregate_rows(*, rows: tuple[dict[str, object], ...], window: int) -> dict[str, object]:
    complete = tuple(row for row in rows if row.get(f'{window}d_complete'))
    returns = [_as_float(row[f'{window}d_return']) for row in complete]
    net_returns = [_as_float(row[f'{window}d_net_return']) for row in complete]
    benchmarks = [_as_float(row[f'{window}d_benchmark_return']) for row in complete]
    excess = [_as_float(row[f'{window}d_excess_return']) for row in complete]
    net_excess = [_as_float(row[f'{window}d_net_excess_return']) for row in complete]
    return {
        'rebalance_date_count': len({row['rebalance_date'] for row in rows}),
        'pick_count': len(rows),
        'complete_count': len(complete),
        'mean_forward_return': _mean(returns),
        'median_forward_return': None if not returns else median(returns),
        'mean_net_return': _mean(net_returns),
        'median_net_return': None if not net_returns else median(net_returns),
        'mean_benchmark_return': _mean(benchmarks),
        'gross_mean_excess_return': _mean(excess),
        'net_mean_excess_return': _mean(net_excess),
        'median_excess_return': None if not excess else median(excess),
        'median_net_excess_return': None if not net_excess else median(net_excess),
        'hit_rate_vs_benchmark': _mean([1.0 if value > 0.0 else 0.0 for value in excess]),
        'positive_return_rate': _mean([1.0 if value > 0.0 else 0.0 for value in returns]),
    }


def _topn_vs_naive_rows(group_summary_rows: tuple[dict[str, object], ...], *, windows: tuple[int, ...]) -> tuple[dict[str, object], ...]:
    comparisons = (
        ('v2_top10_minus_equal_weight', 'v2_raw_top_10', 'naive_equal_weight_feature_complete'),
        ('v2_top10_minus_naive_6m_momentum', 'v2_raw_top_10', 'naive_return_6m_top_10'),
        ('v2_top10_minus_naive_6m_rs', 'v2_raw_top_10', 'naive_rs_6m_top_10'),
        ('v2_BUY_minus_naive_6m_rs', 'v2_BUY', 'naive_rs_6m_top_10'),
        ('v2_BUY_minus_v2_AVOID', 'v2_BUY', 'v2_AVOID'),
        ('v2_top20_minus_bottom20', 'v2_raw_top_20', 'v2_bottom_20_raw_rank'),
    )
    by_key = {(row['group'], row['forward_window_trading_days']): row for row in group_summary_rows}
    rows = []
    for comparison, left_name, right_name in comparisons:
        for window in windows:
            left = by_key.get((left_name, window))
            right = by_key.get((right_name, window))
            rows.append(
                {
                    'comparison': comparison,
                    'forward_window_trading_days': window,
                    'left_group': left_name,
                    'right_group': right_name,
                    'left_complete_count': None if left is None else left['complete_count'],
                    'right_complete_count': None if right is None else right['complete_count'],
                    'net_mean_excess_spread': _optional_difference(left, right, 'net_mean_excess_return'),
                    'gross_mean_excess_spread': _optional_difference(left, right, 'gross_mean_excess_return'),
                    'hit_rate_spread': _optional_difference(left, right, 'hit_rate_vs_benchmark'),
                    'positive_return_rate_spread': _optional_difference(left, right, 'positive_return_rate'),
                }
            )
    return tuple(rows)


def _technical_validity(
    snapshots: tuple[_SnapshotContext, ...],
    *,
    rebalance_dates: tuple[date, ...],
    windows: tuple[int, ...],
) -> dict[str, object]:
    ranked_counts = [snapshot.result.snapshot.ranked_count for snapshot in snapshots]
    missing_exits = sum(len(snapshot.result.missing_exit_rows) for snapshot in snapshots)
    return {
        'technical_valid': bool(snapshots) and all(snapshot.result.snapshot.snapshot_valid for snapshot in snapshots),
        'rebalance_date_count': len(rebalance_dates),
        'rebalance_dates_identical_for_all_groups': True,
        'forward_windows_identical_for_all_groups': True,
        'requested_rebalance_dates': [day.isoformat() for day in rebalance_dates],
        'forward_windows': list(windows),
        'valid_snapshot_count': sum(1 for snapshot in snapshots if snapshot.result.snapshot.snapshot_valid),
        'ranked_count_total': sum(ranked_counts),
        'ranked_count_min': min(ranked_counts) if ranked_counts else 0,
        'ranked_count_max': max(ranked_counts) if ranked_counts else 0,
        'complete_return_counts': {
            str(window): sum(1 for snapshot in snapshots for row in snapshot.rows if row.get(f'{window}d_complete'))
            for window in windows
        },
        'missing_forward_exit_count': missing_exits,
    }


def _quality_answers(
    group_summary_rows: tuple[dict[str, object], ...],
    topn_vs_naive_rows: tuple[dict[str, object], ...],
    *,
    windows: tuple[int, ...],
) -> dict[str, object]:
    comparisons = {(row['comparison'], row['forward_window_trading_days']): row for row in topn_vs_naive_rows}
    answers = {
        'v2_top10_beats_equal_weight_universe': _all_positive(comparisons, 'v2_top10_minus_equal_weight', windows),
        'v2_top10_beats_naive_6m_momentum': _all_positive(comparisons, 'v2_top10_minus_naive_6m_momentum', windows),
        'v2_top10_beats_naive_6m_relative_strength': _all_positive(comparisons, 'v2_top10_minus_naive_6m_rs', windows),
        'v2_BUY_beats_naive_6m_relative_strength': _all_positive(comparisons, 'v2_BUY_minus_naive_6m_rs', windows),
        'v2_AVOID_performs_worse_than_BUY_or_top_groups': _all_positive(comparisons, 'v2_BUY_minus_v2_AVOID', windows),
        'v2_bottom20_performs_worse_than_top20': _all_positive(comparisons, 'v2_top20_minus_bottom20', windows),
    }
    answers['v2_adds_value_beyond_simple_momentum_rs'] = (
        answers['v2_top10_beats_naive_6m_momentum'] and answers['v2_top10_beats_naive_6m_relative_strength']
    )
    answers['one_naive_baseline_dominates_all_v2_groups'] = _one_naive_dominates_all_v2(group_summary_rows, windows=windows)
    answers['v2_clearly_underperforms_simple_naive'] = _clearly_underperforms_naive(comparisons, windows=windows)
    answers['evidence_strong_enough_to_continue_wider_backtest'] = not answers['one_naive_baseline_dominates_all_v2_groups']
    return answers


def _all_positive(comparisons: dict[tuple[str, int], dict[str, object]], name: str, windows: tuple[int, ...]) -> bool:
    values = [_as_optional_float(comparisons[(name, window)]['net_mean_excess_spread']) for window in windows if (name, window) in comparisons]
    return bool(values) and all(value is not None and value > 0.0 for value in values)


def _one_naive_dominates_all_v2(group_summary_rows: tuple[dict[str, object], ...], *, windows: tuple[int, ...]) -> bool:
    v2_groups = {'v2_raw_top_5', 'v2_raw_top_10', 'v2_raw_top_20', 'v2_BUY', 'v2_BUY+WATCH', 'v2_REVIEW', 'v2_AVOID'}
    by_window: dict[int, dict[str, float]] = {}
    for window in windows:
        by_window[window] = {
            str(row['group']): _as_float(row['net_mean_excess_return'])
            for row in group_summary_rows
            if row['forward_window_trading_days'] == window and _as_optional_float(row['net_mean_excess_return']) is not None
        }
    naive_groups = [
        row['group']
        for row in group_summary_rows
        if row['group_category'] == 'naive' and all(row['group'] in by_window[window] for window in windows)
    ]
    for naive in sorted(set(naive_groups)):
        if all(
            all(by_window[window][naive] > by_window[window].get(v2_group, float('-inf')) for v2_group in v2_groups)
            for window in windows
        ):
            return True
    return False


def _clearly_underperforms_naive(comparisons: dict[tuple[str, int], dict[str, object]], *, windows: tuple[int, ...]) -> bool:
    names = ('v2_top10_minus_naive_6m_momentum', 'v2_top10_minus_naive_6m_rs', 'v2_BUY_minus_naive_6m_rs')
    for name in names:
        for window in windows:
            row = comparisons.get((name, window))
            value = None if row is None else _as_optional_float(row['net_mean_excess_spread'])
            if value is None or value > -0.02:
                return False
    return True


def _methodology_suspicious(technical: dict[str, object]) -> bool:
    return not bool(technical['rebalance_dates_identical_for_all_groups']) or not bool(technical['forward_windows_identical_for_all_groups'])


def _raw_top(rows: tuple[dict[str, object], ...], limit: int) -> tuple[dict[str, object], ...]:
    return tuple(row for row in rows if int(row['raw_rank']) <= limit)


def _bottom_by_raw_rank(rows: tuple[dict[str, object], ...], limit: int) -> tuple[dict[str, object], ...]:
    return tuple(sorted(rows, key=lambda row: (-int(row['raw_rank']), str(row['ticker'])))[:limit])


def _top_by_field(rows: tuple[dict[str, object], ...], field: str, limit: int) -> tuple[dict[str, object], ...]:
    valid = tuple(row for row in rows if _as_optional_float(row.get(field)) is not None)
    return tuple(sorted(valid, key=lambda row: (-_as_float(row[field]), str(row['ticker'])))[:limit])


def _trend_rs_top20(rows: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
    valid = tuple(
        row for row in rows
        if bool(row.get('above_sma200')) and _as_optional_float(row.get('return_6m')) is not None and _as_float(row['return_6m']) > 0.0
    )
    return _top_by_field(valid, 'relative_strength_6m', 20)


def _optional_difference(left: dict[str, object] | None, right: dict[str, object] | None, field: str) -> float | None:
    if left is None or right is None:
        return None
    left_value = _as_optional_float(left.get(field))
    right_value = _as_optional_float(right.get(field))
    if left_value is None or right_value is None:
        return None
    return left_value - right_value


def _render_markdown(result: BaselineNaiveComparisonResult) -> str:
    lines = [
        '# Baseline Naive Comparison Summary',
        '',
        '## Technical Validity',
        f"- Decision recommendation: `{result.decision_recommendation}`",
        f"- Rebalance dates: `{list(result.requested_rebalance_dates)}`",
        f"- Valid snapshots: {result.technical_validity['valid_snapshot_count']}/{result.technical_validity['rebalance_date_count']}",
        f"- Ranked count total/range: {result.technical_validity['ranked_count_total']} / {result.technical_validity['ranked_count_min']}-{result.technical_validity['ranked_count_max']}",
        f"- Complete return counts: `{result.technical_validity['complete_return_counts']}`",
        f"- Missing exits: {result.technical_validity['missing_forward_exit_count']}",
        f"- Benchmark: `{result.benchmark_ticker}`",
        f"- Close source: `{result.close_input_source}`",
        '',
        '## Quality Answers',
    ]
    for key, value in result.quality_answers.items():
        lines.append(f'- {key}: `{value}`')
    lines.extend(['', '## Decision Reasons'])
    lines.extend(f'- `{reason}`' for reason in result.decision_reasons)
    return '\n'.join(lines) + '\n'


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
    if value is None or value == '':
        return None
    return _as_float(value)

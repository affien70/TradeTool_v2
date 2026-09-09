from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median

from tradetool.diagnostics.monthly_holdout_backtest import MonthlyHoldoutBacktestResult, build_monthly_holdout_backtest

DECISION_BASELINE_COMPARISON = 'continue_to_baseline_and_naive_comparison'
DECISION_UNDERPERFORMANCE = 'investigate_holdout_underperformance_no_tuning'
DECISION_EXTEND_WINDOW = 'extend_holdout_window_before_decision'
DECISION_BLOCKED_DATA = 'blocked_data_or_alignment_issue'
EXPECTED_REPORT_FILES = (
    'monthly_holdout_group_comparison.csv',
    'monthly_holdout_period_breakdown.csv',
    'monthly_holdout_review_decision.csv',
    'monthly_holdout_review_summary.json',
    'monthly_holdout_review_summary.md',
    'monthly_holdout_signal_quality.csv',
    'monthly_holdout_top_bottom_spread.csv',
)


@dataclass(frozen=True, slots=True)
class MonthlyHoldoutReviewResult:
    monthly_result: MonthlyHoldoutBacktestResult
    technical_validity: dict[str, object]
    group_comparison_rows: tuple[dict[str, object], ...]
    signal_quality_rows: tuple[dict[str, object], ...]
    top_bottom_spread_rows: tuple[dict[str, object], ...]
    period_breakdown_rows: tuple[dict[str, object], ...]
    alpha_review: dict[str, object]
    red_flags: dict[str, object]
    decision_recommendation: str
    decision_reasons: tuple[str, ...]
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'technical_validity': self.technical_validity,
            'alpha_review': self.alpha_review,
            'red_flags': self.red_flags,
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


def build_monthly_holdout_review(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str,
    rebalance_start_date: date,
    rebalance_end_date: date,
    data_source: str,
    windows: tuple[int, ...] = (20, 60),
    universe_db_path: str | Path | None = None,
    monthly_builder=build_monthly_holdout_backtest,
) -> MonthlyHoldoutReviewResult:
    kwargs = {
        'db_path': db_path,
        'universe_id': universe_id,
        'benchmark_ticker': benchmark_ticker,
        'rebalance_start_date': rebalance_start_date,
        'rebalance_end_date': rebalance_end_date,
        'data_source': data_source,
        'windows': windows,
    }
    if universe_db_path is not None:
        kwargs['universe_db_path'] = universe_db_path
    monthly = monthly_builder(**kwargs)
    group_rows = _group_comparison(monthly)
    signal_rows = tuple(row for row in group_rows if row['category'] == 'trade_signal')
    spread_rows = _top_bottom_spreads(group_rows, windows=monthly.forward_windows)
    period_rows = _period_breakdown(monthly)
    technical = _technical_validity(monthly)
    alpha = _alpha_review(group_rows=group_rows, period_rows=period_rows, windows=monthly.forward_windows)
    red_flags = _red_flags(group_rows=group_rows, spread_rows=spread_rows, technical=technical, alpha=alpha)
    decision, reasons = recommend_review_next_action(technical=technical, alpha=alpha, red_flags=red_flags)
    return MonthlyHoldoutReviewResult(
        monthly_result=monthly,
        technical_validity=technical,
        group_comparison_rows=group_rows,
        signal_quality_rows=signal_rows,
        top_bottom_spread_rows=spread_rows,
        period_breakdown_rows=period_rows,
        alpha_review=alpha,
        red_flags=red_flags,
        decision_recommendation=decision,
        decision_reasons=reasons,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_monthly_holdout_review_outputs(*, result: MonthlyHoldoutReviewResult, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'monthly_holdout_review_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'monthly_holdout_review_summary.md').write_text(_render_markdown(result), encoding='utf-8')
    _write_csv(path / 'monthly_holdout_period_breakdown.csv', result.period_breakdown_rows)
    _write_csv(path / 'monthly_holdout_group_comparison.csv', result.group_comparison_rows)
    _write_csv(path / 'monthly_holdout_signal_quality.csv', result.signal_quality_rows)
    _write_csv(path / 'monthly_holdout_top_bottom_spread.csv', result.top_bottom_spread_rows)
    _write_csv(
        path / 'monthly_holdout_review_decision.csv',
        [{'decision_recommendation': result.decision_recommendation, 'decision_reasons': '; '.join(result.decision_reasons)}],
    )


def recommend_review_next_action(
    *,
    technical: dict[str, object],
    alpha: dict[str, object],
    red_flags: dict[str, object],
) -> tuple[str, tuple[str, ...]]:
    if not technical['technical_valid'] or technical['missing_forward_exit_count']:
        return DECISION_BLOCKED_DATA, ('data_or_alignment_issue_prevents_holdout_review',)
    if int(technical['rebalance_count']) < 6:
        return DECISION_EXTEND_WINDOW, ('too_few_monthly_rebalances_for_stable_review',)
    if red_flags['buy_watch_underperformance_severe']:
        return DECISION_UNDERPERFORMANCE, ('buy_or_top_groups_underperformed_severely_no_tuning',)
    return DECISION_BASELINE_COMPARISON, (f"alpha_evidence_{alpha['overall_performance_pattern']}_compare_to_baseline_and_naive",)


def _technical_validity(monthly: MonthlyHoldoutBacktestResult) -> dict[str, object]:
    ranked_counts = [int(row['ranked_count']) for row in monthly.monthly_rebalance_rows]
    return {
        'rebalance_count': monthly.snapshot_count,
        'valid_snapshot_count': monthly.valid_snapshot_count,
        'technical_valid': monthly.snapshot_count > 0 and monthly.snapshot_count == monthly.valid_snapshot_count,
        'ranked_count_total': monthly.total_ranked_count,
        'ranked_count_min': min(ranked_counts) if ranked_counts else 0,
        'ranked_count_max': max(ranked_counts) if ranked_counts else 0,
        'complete_return_counts': {
            str(window): sum(1 for row in monthly.pick_rows if row.get(f'{window}d_complete'))
            for window in monthly.forward_windows
        },
        'missing_forward_exit_count': len(monthly.missing_exit_rows),
        'benchmark_ticker': monthly.benchmark_ticker,
        'close_input_source': monthly.close_input_source,
    }


def _group_comparison(monthly: MonthlyHoldoutBacktestResult) -> tuple[dict[str, object], ...]:
    specs = (
        ('top_n', 'raw_top_5', lambda row: int(row['raw_rank']) <= 5),
        ('top_n', 'raw_top_10', lambda row: int(row['raw_rank']) <= 10),
        ('top_n', 'raw_top_20', lambda row: int(row['raw_rank']) <= 20),
        ('trade_signal', 'BUY', lambda row: row.get('trade_signal') == 'BUY'),
        ('trade_signal', 'BUY+WATCH', lambda row: row.get('trade_signal') in {'BUY', 'WATCH'}),
        ('trade_signal', 'REVIEW', lambda row: row.get('trade_signal') == 'REVIEW'),
        ('trade_signal', 'AVOID', lambda row: row.get('trade_signal') == 'AVOID'),
        ('candidate_type', 'Stable Leader', lambda row: row.get('candidate_type') == 'Stable Leader'),
        ('candidate_type', 'Early Breakout', lambda row: row.get('candidate_type') == 'Early Breakout'),
        ('candidate_type', 'Extended Runner', lambda row: row.get('candidate_type') == 'Extended Runner'),
        ('candidate_type', 'Rebound Case', lambda row: row.get('candidate_type') == 'Rebound Case'),
        ('candidate_type', 'Reject', lambda row: row.get('candidate_type') == 'Reject'),
    )
    rows = []
    for category, group, predicate in specs:
        group_rows = tuple(row for row in monthly.pick_rows if predicate(row))
        for window in monthly.forward_windows:
            rows.append({'category': category, **_aggregate_rows(group=group, rows=group_rows, window=window)})
    return tuple(rows)


def _top_bottom_spreads(group_rows: tuple[dict[str, object], ...], *, windows: tuple[int, ...]) -> tuple[dict[str, object], ...]:
    comparisons = (
        ('BUY_minus_AVOID', 'BUY', 'AVOID'),
        ('BUY+WATCH_minus_AVOID', 'BUY+WATCH', 'AVOID'),
        ('raw_top_10_minus_AVOID', 'raw_top_10', 'AVOID'),
        ('Stable Leader_minus_Reject', 'Stable Leader', 'Reject'),
    )
    by_key = {(row['group'], row['forward_window_trading_days']): row for row in group_rows}
    rows: list[dict[str, object]] = []
    for name, stronger, weaker in comparisons:
        for window in windows:
            left = by_key.get((stronger, window))
            right = by_key.get((weaker, window))
            rows.append(
                {
                    'comparison': name,
                    'forward_window_trading_days': window,
                    'stronger_group': stronger,
                    'weaker_group': weaker,
                    'stronger_complete_count': None if left is None else left['complete_count'],
                    'weaker_complete_count': None if right is None else right['complete_count'],
                    'mean_net_excess_spread': _optional_difference(left, right, 'mean_net_excess_return'),
                    'median_net_excess_spread': _optional_difference(left, right, 'median_net_excess_return'),
                    'hit_rate_spread': _optional_difference(left, right, 'hit_rate_vs_benchmark'),
                    'positive_return_rate_spread': _optional_difference(left, right, 'positive_return_rate'),
                }
            )
    return tuple(rows)


def _period_breakdown(monthly: MonthlyHoldoutBacktestResult) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for rebalance_date in monthly.requested_rebalance_dates:
        period_rows = tuple(row for row in monthly.pick_rows if row.get('rebalance_date') == rebalance_date)
        for window in monthly.forward_windows:
            top10 = _aggregate_rows(group='raw_top_10', rows=tuple(row for row in period_rows if int(row['raw_rank']) <= 10), window=window)
            buy = _aggregate_rows(group='BUY', rows=tuple(row for row in period_rows if row.get('trade_signal') == 'BUY'), window=window)
            buy_watch = _aggregate_rows(group='BUY+WATCH', rows=tuple(row for row in period_rows if row.get('trade_signal') in {'BUY', 'WATCH'}), window=window)
            avoid = _aggregate_rows(group='AVOID', rows=tuple(row for row in period_rows if row.get('trade_signal') == 'AVOID'), window=window)
            stable = _aggregate_rows(group='Stable Leader', rows=tuple(row for row in period_rows if row.get('candidate_type') == 'Stable Leader'), window=window)
            reject = _aggregate_rows(group='Reject', rows=tuple(row for row in period_rows if row.get('candidate_type') == 'Reject'), window=window)
            rows.append(
                {
                    'rebalance_date': rebalance_date,
                    'forward_window_trading_days': window,
                    'ranked_count': len(period_rows),
                    'raw_top_10_mean_net_excess': top10['mean_net_excess_return'],
                    'buy_mean_net_excess': buy['mean_net_excess_return'],
                    'buy_watch_mean_net_excess': buy_watch['mean_net_excess_return'],
                    'avoid_mean_net_excess': avoid['mean_net_excess_return'],
                    'stable_leader_mean_net_excess': stable['mean_net_excess_return'],
                    'reject_mean_net_excess': reject['mean_net_excess_return'],
                    'raw_top_10_minus_avoid_net_excess': _value_difference(top10['mean_net_excess_return'], avoid['mean_net_excess_return']),
                    'buy_minus_avoid_net_excess': _value_difference(buy['mean_net_excess_return'], avoid['mean_net_excess_return']),
                    'top10_hit_rate_vs_benchmark': top10['hit_rate_vs_benchmark'],
                    'buy_hit_rate_vs_benchmark': buy['hit_rate_vs_benchmark'],
                }
            )
    return tuple(rows)


def _alpha_review(
    *,
    group_rows: tuple[dict[str, object], ...],
    period_rows: tuple[dict[str, object], ...],
    windows: tuple[int, ...],
) -> dict[str, object]:
    by_key = {(row['group'], row['forward_window_trading_days']): row for row in group_rows}
    top10_rows = [by_key[('raw_top_10', window)] for window in windows if ('raw_top_10', window) in by_key]
    buy_rows = [by_key[('BUY', window)] for window in windows if ('BUY', window) in by_key]
    buy_watch_rows = [by_key[('BUY+WATCH', window)] for window in windows if ('BUY+WATCH', window) in by_key]
    positive_months = {
        str(window): sum(
            1
            for row in period_rows
            if row['forward_window_trading_days'] == window and _as_optional_float(row['raw_top_10_mean_net_excess']) is not None and _as_float(row['raw_top_10_mean_net_excess']) > 0
        )
        for window in windows
    }
    total_months = {str(window): sum(1 for row in period_rows if row['forward_window_trading_days'] == window) for window in windows}
    rates = {
        window: positive_months[str(window)] / total_months[str(window)]
        for window in windows
        if total_months[str(window)]
    }
    return {
        'top10_mean_net_excess_by_window': {str(row['forward_window_trading_days']): row['mean_net_excess_return'] for row in top10_rows},
        'buy_mean_net_excess_by_window': {str(row['forward_window_trading_days']): row['mean_net_excess_return'] for row in buy_rows},
        'buy_watch_mean_net_excess_by_window': {str(row['forward_window_trading_days']): row['mean_net_excess_return'] for row in buy_watch_rows},
        'raw_top10_positive_month_counts': positive_months,
        'raw_top10_positive_month_rates': {str(window): rates[window] for window in rates},
        'overall_performance_pattern': _performance_pattern(rates, period_rows),
    }


def _red_flags(
    *,
    group_rows: tuple[dict[str, object], ...],
    spread_rows: tuple[dict[str, object], ...],
    technical: dict[str, object],
    alpha: dict[str, object],
) -> dict[str, object]:
    severe = _severe_underperformance(group_rows=group_rows, spread_rows=spread_rows, alpha=alpha)
    return {
        'buy_watch_underperformance_severe': severe,
        'avoid_correctly_worse_than_stronger_groups': _spread_positive(spread_rows, 'BUY+WATCH_minus_AVOID'),
        'ranking_separates_better_worse_buckets': _spread_positive(spread_rows, 'raw_top_10_minus_AVOID'),
        'supports_production_readiness': False,
        'supports_further_historical_testing': bool(technical['technical_valid']) and not technical['missing_forward_exit_count'],
        'supports_immediate_tuning': False,
    }


def _severe_underperformance(
    *,
    group_rows: tuple[dict[str, object], ...],
    spread_rows: tuple[dict[str, object], ...],
    alpha: dict[str, object],
) -> bool:
    by_key = {(row['group'], row['forward_window_trading_days']): row for row in group_rows}
    windows = {row['forward_window_trading_days'] for row in group_rows}
    for window in windows:
        top10 = by_key.get(('raw_top_10', window))
        buy = by_key.get(('BUY', window))
        if top10 is None or buy is None:
            return False
        top10_excess = _as_optional_float(top10['mean_net_excess_return'])
        buy_excess = _as_optional_float(buy['mean_net_excess_return'])
        top10_hit = _as_optional_float(top10['hit_rate_vs_benchmark'])
        buy_hit = _as_optional_float(buy['hit_rate_vs_benchmark'])
        if top10_excess is None or buy_excess is None or top10_hit is None or buy_hit is None:
            return False
        if top10_excess > -0.05 or buy_excess > -0.05 or top10_hit > 0.35 or buy_hit > 0.35:
            return False
    bad_spreads = [
        row for row in spread_rows
        if row['comparison'] in {'BUY_minus_AVOID', 'raw_top_10_minus_AVOID'} and _as_optional_float(row['mean_net_excess_spread']) is not None
    ]
    return bool(bad_spreads) and all(_as_float(row['mean_net_excess_spread']) <= -0.05 for row in bad_spreads)


def _performance_pattern(rates: dict[int, float], period_rows: tuple[dict[str, object], ...]) -> str:
    if not rates:
        return 'unclear'
    if all(rate >= 0.70 for rate in rates.values()):
        return 'consistently_positive'
    if all(rate <= 0.30 for rate in rates.values()):
        return 'consistently_weak'
    net_values = [
        _as_float(row['raw_top_10_mean_net_excess'])
        for row in period_rows
        if _as_optional_float(row['raw_top_10_mean_net_excess']) is not None
    ]
    if net_values and sum(net_values) > 0 and max(net_values) > abs(sum(net_values)) * 0.5:
        return 'dominated_by_few_months'
    return 'mixed_or_unclear'


def _spread_positive(spread_rows: tuple[dict[str, object], ...], comparison: str) -> bool:
    values = [
        _as_float(row['mean_net_excess_spread'])
        for row in spread_rows
        if row['comparison'] == comparison and _as_optional_float(row['mean_net_excess_spread']) is not None
    ]
    return bool(values) and sum(1 for value in values if value > 0.0) > len(values) / 2


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


def _optional_difference(left: dict[str, object] | None, right: dict[str, object] | None, field: str) -> float | None:
    if left is None or right is None:
        return None
    return _value_difference(left.get(field), right.get(field))


def _value_difference(left: object, right: object) -> float | None:
    if _as_optional_float(left) is None or _as_optional_float(right) is None:
        return None
    return _as_float(left) - _as_float(right)


def _render_markdown(result: MonthlyHoldoutReviewResult) -> str:
    technical = result.technical_validity
    lines = [
        '# Monthly Holdout Review Summary',
        '',
        '## Technical Validity',
        f"- Decision recommendation: `{result.decision_recommendation}`",
        f"- Rebalance count: {technical['rebalance_count']}",
        f"- Valid snapshots: {technical['valid_snapshot_count']}/{technical['rebalance_count']}",
        f"- Ranked count total/range: {technical['ranked_count_total']} / {technical['ranked_count_min']}-{technical['ranked_count_max']}",
        f"- Complete return counts: `{technical['complete_return_counts']}`",
        f"- Missing exits: {technical['missing_forward_exit_count']}",
        f"- Benchmark: `{technical['benchmark_ticker']}`",
        f"- Close source: `{technical['close_input_source']}`",
        '',
        '## Alpha And Separation',
        f"- Pattern: `{result.alpha_review['overall_performance_pattern']}`",
        f"- Top 10 net excess by window: `{result.alpha_review['top10_mean_net_excess_by_window']}`",
        f"- BUY net excess by window: `{result.alpha_review['buy_mean_net_excess_by_window']}`",
        f"- BUY+WATCH net excess by window: `{result.alpha_review['buy_watch_mean_net_excess_by_window']}`",
        '',
        '## Red Flags',
    ]
    for key, value in result.red_flags.items():
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

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median

from tradetool.diagnostics.challenger_comparison import (
    CHALLENGER_ID as FAILED_CHALLENGER_ID,
    GROUP_V2_RAW_TOP10,
    select_challenger_rs6m_trend_risk,
)
from tradetool.diagnostics.holdout_forward_returns import ForwardReturnResult, build_holdout_forward_returns
from tradetool.diagnostics.incumbent_baseline import BASELINE_ID, select_incumbent_baseline
from tradetool.diagnostics.monthly_holdout_backtest import ROUND_TRIP_COST_RATE, generate_monthly_rebalance_dates
from tradetool.policy.trade_policy import MAX_WATCH_DISTANCE_TO_SMA200, MIN_ACCEPTABLE_TRADED_VALUE

CHALLENGER_ID = 'challenger_rs6m_trend_liquidity_no_drawdown_v0'
DECISION_PROMOTE = 'promote_no_drawdown_challenger_for_extended_testing'
DECISION_KEEP_INCUMBENT = 'keep_incumbent_baseline'
DECISION_INVESTIGATE_FILTER_DAMAGE = 'investigate_remaining_filter_damage'
DECISION_FIX_METHODOLOGY = 'fix_challenger_no_drawdown_methodology'
EXPECTED_REPORT_FILES = (
    'challenger_no_drawdown_summary.md',
    'challenger_no_drawdown_summary.json',
    'challenger_no_drawdown_group_summary.csv',
    'challenger_no_drawdown_monthly.csv',
    'challenger_no_drawdown_overlap.csv',
    'challenger_no_drawdown_removed_vs_incumbent.csv',
    'challenger_no_drawdown_decision.csv',
)
GROUP_INCUMBENT = BASELINE_ID
GROUP_FAILED_CHALLENGER = FAILED_CHALLENGER_ID
GROUP_NO_DRAWDOWN_CHALLENGER = CHALLENGER_ID
COMPARE_GROUPS = (GROUP_INCUMBENT, GROUP_FAILED_CHALLENGER, GROUP_NO_DRAWDOWN_CHALLENGER, GROUP_V2_RAW_TOP10)


@dataclass(frozen=True, slots=True)
class ChallengerNoDrawdownComparisonResult:
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
    failed_challenger_id: str
    challenger_id: str
    technical_validity: dict[str, object]
    comparison_summary: dict[str, object]
    decision_recommendation: str
    decision_reasons: tuple[str, ...]
    group_summary_rows: tuple[dict[str, object], ...]
    monthly_rows: tuple[dict[str, object], ...]
    overlap_rows: tuple[dict[str, object], ...]
    removed_vs_incumbent_rows: tuple[dict[str, object], ...]
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
            'failed_challenger_id': self.failed_challenger_id,
            'challenger_id': self.challenger_id,
            'challenger_rule': {
                'hard_requirements': [
                    'above_sma200 == true',
                    'return_6m > 0',
                    'relative_strength_6m > 0',
                    f'average_traded_value_20 >= {MIN_ACCEPTABLE_TRADED_VALUE} when present',
                    f'distance_to_sma200 <= {MAX_WATCH_DISTANCE_TO_SMA200} when present',
                    'no drawdown gate',
                ],
                'sort': [
                    'relative_strength_6m descending',
                    'relative_strength_3m descending',
                    'return_6m descending',
                    'ticker ascending',
                ],
                'selection_limit': 10,
                'research_only': True,
            },
            'technical_validity': self.technical_validity,
            'comparison_summary': self.comparison_summary,
            'decision_recommendation': self.decision_recommendation,
            'decision_reasons': list(self.decision_reasons),
            'output_files': list(EXPECTED_REPORT_FILES),
            'leakage_controls': {
                'same_rebalance_dates_for_all_groups': True,
                'same_forward_windows_for_all_groups': True,
                'same_benchmark_for_all_groups': True,
                'same_transaction_cost_for_all_groups': True,
                'feature_rows_capped_at_each_rebalance_date': True,
                'forward_returns_used_to_change_selection': False,
                'tuning_applied': False,
                'production_ranking_changed': False,
                'trade_policy_thresholds_changed': False,
                'candidate_type_rules_changed': False,
                'ml_score_calculated': False,
                'holdings_adjustment_applied': False,
                'screener_ui_changed': False,
            },
            'generated_at_utc': self.generated_at_utc,
        }


@dataclass(frozen=True, slots=True)
class _SnapshotContext:
    result: ForwardReturnResult
    rows: tuple[dict[str, object], ...]
    groups: dict[str, tuple[dict[str, object], ...]]


def build_challenger_no_drawdown_comparison(
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
) -> ChallengerNoDrawdownComparisonResult:
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
    monthly_rows = _monthly_rows(grouped_rows, windows=normalized_windows)
    overlap_rows = _overlap_rows(snapshots=snapshots, windows=normalized_windows)
    removed_rows = _removed_vs_incumbent_rows(snapshots=snapshots, windows=normalized_windows)
    technical = _technical_validity(snapshots, rebalance_dates=rebalance_dates, windows=normalized_windows)
    comparison_summary = _comparison_summary(group_summary, monthly_rows, overlap_rows, windows=normalized_windows)
    decision, reasons = recommend_next_action(technical=technical, comparison_summary=comparison_summary)
    decision_rows = _decision_rows(decision=decision, reasons=reasons, comparison_summary=comparison_summary)
    return ChallengerNoDrawdownComparisonResult(
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
        failed_challenger_id=FAILED_CHALLENGER_ID,
        challenger_id=CHALLENGER_ID,
        technical_validity=technical,
        comparison_summary=comparison_summary,
        decision_recommendation=decision,
        decision_reasons=reasons,
        group_summary_rows=group_summary,
        monthly_rows=monthly_rows,
        overlap_rows=overlap_rows,
        removed_vs_incumbent_rows=removed_rows,
        decision_rows=decision_rows,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_challenger_no_drawdown_comparison_outputs(
    *,
    result: ChallengerNoDrawdownComparisonResult,
    out_dir: str | Path,
) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'challenger_no_drawdown_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'challenger_no_drawdown_summary.md').write_text(_render_markdown(result), encoding='utf-8')
    _write_csv(path / 'challenger_no_drawdown_group_summary.csv', result.group_summary_rows)
    _write_csv(path / 'challenger_no_drawdown_monthly.csv', result.monthly_rows)
    _write_csv(path / 'challenger_no_drawdown_overlap.csv', result.overlap_rows)
    _write_csv(path / 'challenger_no_drawdown_removed_vs_incumbent.csv', result.removed_vs_incumbent_rows)
    _write_csv(path / 'challenger_no_drawdown_decision.csv', result.decision_rows)


def select_challenger_rs6m_trend_liquidity_no_drawdown(
    rows: tuple[dict[str, object], ...],
    *,
    limit: int = 10,
) -> tuple[dict[str, object], ...]:
    eligible = tuple(row for row in rows if _no_drawdown_eligible(row))
    return tuple(
        sorted(
            eligible,
            key=lambda row: (
                -_as_float(row['relative_strength_6m']),
                -_as_float(row.get('relative_strength_3m')),
                -_as_float(row.get('return_6m')),
                str(row['ticker']),
            ),
        )[:limit]
    )


def recommend_next_action(
    *,
    technical: dict[str, object],
    comparison_summary: dict[str, object],
) -> tuple[str, tuple[str, ...]]:
    if not technical['technical_valid'] or not technical['methodology_clean']:
        return DECISION_FIX_METHODOLOGY, ('comparison_dates_windows_or_data_are_inconsistent',)
    spreads = comparison_summary['spreads']
    no_vs_inc_60 = _as_optional_float(spreads['no_drawdown_minus_incumbent'].get('60'))
    no_vs_inc_20 = _as_optional_float(spreads['no_drawdown_minus_incumbent'].get('20'))
    no_vs_failed_60 = _as_optional_float(spreads['no_drawdown_minus_failed_challenger'].get('60'))
    incumbent_only_60 = _as_optional_float(comparison_summary['incumbent_only_mean_net_excess_by_window'].get('60'))
    if no_vs_inc_60 is not None and no_vs_inc_60 > 0.0 and (no_vs_inc_20 is None or no_vs_inc_20 >= 0.0):
        return DECISION_PROMOTE, ('no_drawdown_challenger_beats_incumbent_60d_and_is_not_worse_20d',)
    if (
        no_vs_failed_60 is not None
        and no_vs_failed_60 > 0.0
        and no_vs_inc_60 is not None
        and no_vs_inc_60 < 0.0
        and incumbent_only_60 is not None
        and incumbent_only_60 > 0.0
    ):
        return DECISION_INVESTIGATE_FILTER_DAMAGE, ('no_drawdown_improved_but_remaining_filters_removed_incumbent_winners',)
    return DECISION_KEEP_INCUMBENT, ('no_drawdown_challenger_did_not_beat_incumbent_baseline',)


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
    groups = {
        GROUP_INCUMBENT: select_incumbent_baseline(rows),
        GROUP_FAILED_CHALLENGER: select_challenger_rs6m_trend_risk(rows),
        GROUP_NO_DRAWDOWN_CHALLENGER: select_challenger_rs6m_trend_liquidity_no_drawdown(rows),
        GROUP_V2_RAW_TOP10: _raw_top(rows, 10),
    }
    return _SnapshotContext(result=result, rows=rows, groups=groups)


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


def _no_drawdown_eligible(row: dict[str, object]) -> bool:
    return all(
        (
            bool(row.get('above_sma200')),
            _as_optional_float(row.get('return_6m')) is not None and _as_float(row['return_6m']) > 0.0,
            _as_optional_float(row.get('relative_strength_6m')) is not None and _as_float(row['relative_strength_6m']) > 0.0,
            _optional_minimum(row.get('average_traded_value_20'), MIN_ACCEPTABLE_TRADED_VALUE),
            _optional_maximum(row.get('distance_to_sma200'), MAX_WATCH_DISTANCE_TO_SMA200),
        )
    )


def _group_rows_for_snapshot(snapshot: _SnapshotContext) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            'rebalance_date': snapshot.result.as_of_date,
            'group': group,
            'group_role': 'context_only' if group == GROUP_V2_RAW_TOP10 else 'comparison',
            **row,
        }
        for group in COMPARE_GROUPS
        for row in snapshot.groups.get(group, ())
    )


def _summarize_grouped_rows(
    grouped_rows: tuple[dict[str, object], ...],
    *,
    windows: tuple[int, ...],
) -> tuple[dict[str, object], ...]:
    output = []
    for group in sorted({row['group'] for row in grouped_rows}):
        rows = tuple(row for row in grouped_rows if row['group'] == group)
        for window in windows:
            output.append({'group': group, 'forward_window_trading_days': window, **_aggregate_rows(rows=rows, window=window)})
    return tuple(output)


def _monthly_rows(grouped_rows: tuple[dict[str, object], ...], *, windows: tuple[int, ...]) -> tuple[dict[str, object], ...]:
    output = []
    for rebalance_date in sorted({row['rebalance_date'] for row in grouped_rows}):
        for group in sorted({row['group'] for row in grouped_rows if row['rebalance_date'] == rebalance_date}):
            rows = tuple(row for row in grouped_rows if row['rebalance_date'] == rebalance_date and row['group'] == group)
            for window in windows:
                output.append(
                    {
                        'rebalance_date': rebalance_date,
                        'group': group,
                        'forward_window_trading_days': window,
                        **_aggregate_rows(rows=rows, window=window),
                    }
                )
    return tuple(output)


def _aggregate_rows(*, rows: tuple[dict[str, object], ...], window: int) -> dict[str, object]:
    complete = tuple(row for row in rows if row.get(f'{window}d_complete'))
    returns = [_as_float(row[f'{window}d_return']) for row in complete]
    net_returns = [_as_float(row[f'{window}d_net_return']) for row in complete]
    benchmarks = [_as_float(row[f'{window}d_benchmark_return']) for row in complete]
    excess = [_as_float(row[f'{window}d_excess_return']) for row in complete]
    net_excess = [_as_float(row[f'{window}d_net_excess_return']) for row in complete]
    return {
        'pick_count': len(rows),
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
        'hit_rate_vs_benchmark': _mean([1.0 if value > 0.0 else 0.0 for value in excess]),
        'positive_return_rate': _mean([1.0 if value > 0.0 else 0.0 for value in returns]),
    }


def _overlap_rows(
    *,
    snapshots: tuple[_SnapshotContext, ...],
    windows: tuple[int, ...],
) -> tuple[dict[str, object], ...]:
    output = []
    pairs = (
        (GROUP_INCUMBENT, GROUP_NO_DRAWDOWN_CHALLENGER),
        (GROUP_INCUMBENT, GROUP_FAILED_CHALLENGER),
        (GROUP_FAILED_CHALLENGER, GROUP_NO_DRAWDOWN_CHALLENGER),
    )
    for snapshot in snapshots:
        for left_group, right_group in pairs:
            left_tickers = _ticker_set(snapshot.groups[left_group])
            right_tickers = _ticker_set(snapshot.groups[right_group])
            overlap = sorted(left_tickers & right_tickers)
            left_only = sorted(left_tickers - right_tickers)
            right_only = sorted(right_tickers - left_tickers)
            for window in windows:
                output.append(
                    {
                        'rebalance_date': snapshot.result.as_of_date,
                        'left_group': left_group,
                        'right_group': right_group,
                        'forward_window_trading_days': window,
                        'overlap_count': len(overlap),
                        'overlap_tickers': ';'.join(overlap),
                        'left_only_tickers': ';'.join(left_only),
                        'right_only_tickers': ';'.join(right_only),
                        'left_only_mean_net_excess_return': _mean_for_tickers(snapshot.rows, left_only, window),
                        'right_only_mean_net_excess_return': _mean_for_tickers(snapshot.rows, right_only, window),
                    }
                )
    return tuple(output)


def _removed_vs_incumbent_rows(
    *,
    snapshots: tuple[_SnapshotContext, ...],
    windows: tuple[int, ...],
) -> tuple[dict[str, object], ...]:
    output = []
    for snapshot in snapshots:
        no_drawdown_tickers = _ticker_set(snapshot.groups[GROUP_NO_DRAWDOWN_CHALLENGER])
        failed_tickers = _ticker_set(snapshot.groups[GROUP_FAILED_CHALLENGER])
        for incumbent_rank, row in enumerate(snapshot.groups[GROUP_INCUMBENT], start=1):
            removed = str(row['ticker']) not in no_drawdown_tickers
            item = {
                'rebalance_date': snapshot.result.as_of_date,
                'ticker': row['ticker'],
                'incumbent_rank': incumbent_rank,
                'included_in_no_drawdown_challenger': not removed,
                'included_in_failed_challenger': str(row['ticker']) in failed_tickers,
                'remaining_filter_failures': ';'.join(_remaining_filter_failures(row)),
                'raw_rank': row.get('raw_rank'),
                'raw_score': row.get('raw_score'),
                'trade_signal': row.get('trade_signal'),
                'candidate_type': row.get('candidate_type'),
            }
            for window in windows:
                item[f'{window}d_net_excess_return'] = row.get(f'{window}d_net_excess_return')
                item[f'{window}d_removed_winner'] = removed and _as_optional_float(row.get(f'{window}d_net_excess_return')) is not None and _as_float(row[f'{window}d_net_excess_return']) > 0.0
            output.append(item)
    return tuple(output)


def _remaining_filter_failures(row: dict[str, object]) -> tuple[str, ...]:
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
    stretch = _as_optional_float(row.get('distance_to_sma200'))
    if stretch is not None and stretch > MAX_WATCH_DISTANCE_TO_SMA200:
        failures.append('sma200_stretch')
    return tuple(failures)


def _technical_validity(
    snapshots: tuple[_SnapshotContext, ...],
    *,
    rebalance_dates: tuple[date, ...],
    windows: tuple[int, ...],
) -> dict[str, object]:
    ranked_counts = [snapshot.result.snapshot.ranked_count for snapshot in snapshots]
    missing_exits = sum(len(snapshot.result.missing_exit_rows) for snapshot in snapshots)
    group_pick_counts = {
        group: sum(len(snapshot.groups[group]) for snapshot in snapshots)
        for group in COMPARE_GROUPS
    }
    return {
        'technical_valid': bool(snapshots) and all(snapshot.result.snapshot.snapshot_valid for snapshot in snapshots) and missing_exits == 0,
        'methodology_clean': True,
        'rebalance_date_count': len(rebalance_dates),
        'requested_rebalance_dates': [day.isoformat() for day in rebalance_dates],
        'valid_snapshot_count': sum(1 for snapshot in snapshots if snapshot.result.snapshot.snapshot_valid),
        'ranked_count_total': sum(ranked_counts),
        'ranked_count_min': min(ranked_counts) if ranked_counts else 0,
        'ranked_count_max': max(ranked_counts) if ranked_counts else 0,
        'complete_return_counts': {
            str(window): sum(1 for snapshot in snapshots for row in snapshot.rows if row.get(f'{window}d_complete'))
            for window in windows
        },
        'missing_forward_exit_count': missing_exits,
        'group_pick_counts': group_pick_counts,
    }


def _comparison_summary(
    group_summary_rows: tuple[dict[str, object], ...],
    monthly_rows: tuple[dict[str, object], ...],
    overlap_rows: tuple[dict[str, object], ...],
    *,
    windows: tuple[int, ...],
) -> dict[str, object]:
    by_group = {(row['group'], int(row['forward_window_trading_days'])): row for row in group_summary_rows}
    spreads: dict[str, dict[str, float | None]] = {
        'no_drawdown_minus_incumbent': {},
        'no_drawdown_minus_failed_challenger': {},
        'failed_challenger_minus_incumbent': {},
    }
    monthly_wins: dict[str, dict[str, int]] = {}
    incumbent_only_mean: dict[str, float | None] = {}
    for window in windows:
        spreads['no_drawdown_minus_incumbent'][str(window)] = _optional_difference(
            by_group.get((GROUP_NO_DRAWDOWN_CHALLENGER, window)),
            by_group.get((GROUP_INCUMBENT, window)),
            'mean_net_excess_return',
        )
        spreads['no_drawdown_minus_failed_challenger'][str(window)] = _optional_difference(
            by_group.get((GROUP_NO_DRAWDOWN_CHALLENGER, window)),
            by_group.get((GROUP_FAILED_CHALLENGER, window)),
            'mean_net_excess_return',
        )
        spreads['failed_challenger_minus_incumbent'][str(window)] = _optional_difference(
            by_group.get((GROUP_FAILED_CHALLENGER, window)),
            by_group.get((GROUP_INCUMBENT, window)),
            'mean_net_excess_return',
        )
        monthly_wins[str(window)] = _monthly_win_counts(monthly_rows, window=window)
        incumbent_only_mean[str(window)] = _mean(
            [
                _as_float(row['left_only_mean_net_excess_return'])
                for row in overlap_rows
                if row['left_group'] == GROUP_INCUMBENT
                and row['right_group'] == GROUP_NO_DRAWDOWN_CHALLENGER
                and int(row['forward_window_trading_days']) == window
                and _as_optional_float(row.get('left_only_mean_net_excess_return')) is not None
            ]
        )
    return {
        'spreads': spreads,
        'monthly_win_loss_vs_incumbent_by_window': monthly_wins,
        'incumbent_only_mean_net_excess_by_window': incumbent_only_mean,
        'incumbent': {str(window): by_group.get((GROUP_INCUMBENT, window), {}) for window in windows},
        'failed_challenger': {str(window): by_group.get((GROUP_FAILED_CHALLENGER, window), {}) for window in windows},
        'no_drawdown_challenger': {str(window): by_group.get((GROUP_NO_DRAWDOWN_CHALLENGER, window), {}) for window in windows},
        'v2_raw_top10_context': {str(window): by_group.get((GROUP_V2_RAW_TOP10, window), {}) for window in windows},
    }


def _monthly_win_counts(rows: tuple[dict[str, object], ...], *, window: int) -> dict[str, int]:
    by_key = {(row['rebalance_date'], row['group']): row for row in rows if int(row['forward_window_trading_days']) == window}
    dates = sorted({row['rebalance_date'] for row in rows if int(row['forward_window_trading_days']) == window})
    counts = {'no_drawdown_win': 0, 'incumbent_win': 0, 'tie': 0, 'unavailable': 0}
    for rebalance_date in dates:
        challenger = by_key.get((rebalance_date, GROUP_NO_DRAWDOWN_CHALLENGER))
        incumbent = by_key.get((rebalance_date, GROUP_INCUMBENT))
        spread = _optional_difference(challenger, incumbent, 'mean_net_excess_return')
        if spread is None:
            counts['unavailable'] += 1
        elif spread > 0.0:
            counts['no_drawdown_win'] += 1
        elif spread < 0.0:
            counts['incumbent_win'] += 1
        else:
            counts['tie'] += 1
    return counts


def _decision_rows(
    *,
    decision: str,
    reasons: tuple[str, ...],
    comparison_summary: dict[str, object],
) -> tuple[dict[str, object], ...]:
    spreads = comparison_summary['spreads']
    return (
        {
            'decision_recommendation': decision,
            'decision_reasons': '; '.join(reasons),
            'no_drawdown_minus_incumbent_20d_net_excess': spreads['no_drawdown_minus_incumbent'].get('20'),
            'no_drawdown_minus_incumbent_60d_net_excess': spreads['no_drawdown_minus_incumbent'].get('60'),
            'no_drawdown_minus_failed_challenger_20d_net_excess': spreads['no_drawdown_minus_failed_challenger'].get('20'),
            'no_drawdown_minus_failed_challenger_60d_net_excess': spreads['no_drawdown_minus_failed_challenger'].get('60'),
        },
    )


def _raw_top(rows: tuple[dict[str, object], ...], limit: int) -> tuple[dict[str, object], ...]:
    return tuple(sorted((row for row in rows if int(row['raw_rank']) <= limit), key=lambda row: (int(row['raw_rank']), str(row['ticker']))))


def _ticker_set(rows: tuple[dict[str, object], ...]) -> set[str]:
    return {str(row['ticker']) for row in rows}


def _mean_for_tickers(rows: tuple[dict[str, object], ...], tickers: list[str], window: int) -> float | None:
    by_ticker = {str(row['ticker']): row for row in rows}
    values = [
        _as_float(by_ticker[ticker][f'{window}d_net_excess_return'])
        for ticker in tickers
        if ticker in by_ticker and _as_optional_float(by_ticker[ticker].get(f'{window}d_net_excess_return')) is not None
    ]
    return _mean(values)


def _optional_minimum(value: object, minimum: float) -> bool:
    parsed = _as_optional_float(value)
    return parsed is None or parsed >= minimum


def _optional_maximum(value: object, maximum: float) -> bool:
    parsed = _as_optional_float(value)
    return parsed is None or parsed <= maximum


def _optional_difference(left: dict[str, object] | None, right: dict[str, object] | None, field: str) -> float | None:
    if left is None or right is None:
        return None
    left_value = _as_optional_float(left.get(field))
    right_value = _as_optional_float(right.get(field))
    if left_value is None or right_value is None:
        return None
    return left_value - right_value


def _render_markdown(result: ChallengerNoDrawdownComparisonResult) -> str:
    spreads = result.comparison_summary['spreads']
    lines = [
        '# No-Drawdown Challenger Comparison',
        '',
        f'- Decision recommendation: `{result.decision_recommendation}`',
        f'- Incumbent: `{result.incumbent_id}`',
        f'- Failed challenger: `{result.failed_challenger_id}`',
        f'- No-drawdown challenger: `{result.challenger_id}`',
        f'- Rebalance dates: `{list(result.requested_rebalance_dates)}`',
        f'- Valid snapshots: {result.technical_validity["valid_snapshot_count"]}/{result.technical_validity["rebalance_date_count"]}',
        f'- Ranked count total/range: {result.technical_validity["ranked_count_total"]} / {result.technical_validity["ranked_count_min"]}-{result.technical_validity["ranked_count_max"]}',
        f'- Complete return counts: `{result.technical_validity["complete_return_counts"]}`',
        f'- Missing exits: {result.technical_validity["missing_forward_exit_count"]}',
        f'- No-drawdown minus incumbent 20d net excess: `{spreads["no_drawdown_minus_incumbent"].get("20")}`',
        f'- No-drawdown minus incumbent 60d net excess: `{spreads["no_drawdown_minus_incumbent"].get("60")}`',
        f'- No-drawdown minus failed challenger 20d net excess: `{spreads["no_drawdown_minus_failed_challenger"].get("20")}`',
        f'- No-drawdown minus failed challenger 60d net excess: `{spreads["no_drawdown_minus_failed_challenger"].get("60")}`',
        '',
        '## Decision Reasons',
    ]
    lines.extend(f'- `{reason}`' for reason in result.decision_reasons)
    lines.extend(
        [
            '',
            '## Guardrails',
            '- Research-only diagnostic; no production ranking, Screener UI, policy threshold, ML, or Holdings change.',
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

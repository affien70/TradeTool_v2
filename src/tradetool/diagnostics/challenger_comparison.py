from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median

from tradetool.diagnostics.holdout_forward_returns import ForwardReturnResult, build_holdout_forward_returns
from tradetool.diagnostics.incumbent_baseline import BASELINE_ID, select_incumbent_baseline
from tradetool.diagnostics.monthly_holdout_backtest import ROUND_TRIP_COST_RATE, generate_monthly_rebalance_dates
from tradetool.policy.trade_policy import MAX_WATCH_DISTANCE_TO_SMA200, MIN_ACCEPTABLE_DRAWDOWN, MIN_ACCEPTABLE_TRADED_VALUE

CHALLENGER_ID = 'challenger_rs6m_trend_risk_v0'
DECISION_PROMOTE = 'promote_challenger_for_extended_testing'
DECISION_KEEP_INCUMBENT = 'keep_incumbent_baseline'
DECISION_FILTER_DAMAGE = 'investigate_challenger_filter_damage'
DECISION_FIX_METHODOLOGY = 'fix_challenger_comparison_methodology'
DECISION_BLOCKED_DATA = 'blocked_data_or_alignment_issue'
EXPECTED_REPORT_FILES = (
    'challenger_comparison_summary.json',
    'challenger_comparison_summary.md',
    'challenger_group_summary.csv',
    'challenger_monthly_breakdown.csv',
    'challenger_overlap.csv',
    'challenger_pick_returns.csv',
    'challenger_decision.csv',
)
GROUP_INCUMBENT = BASELINE_ID
GROUP_CHALLENGER = CHALLENGER_ID
GROUP_V2_RAW_TOP10 = 'v2_raw_top10_context_only'
COMPARE_GROUPS = (GROUP_INCUMBENT, GROUP_CHALLENGER, GROUP_V2_RAW_TOP10)


@dataclass(frozen=True, slots=True)
class ChallengerComparisonResult:
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
    challenger_id: str
    incumbent_id: str
    comparison_summary: dict[str, object]
    decision_recommendation: str
    decision_reasons: tuple[str, ...]
    group_summary_rows: tuple[dict[str, object], ...]
    monthly_breakdown_rows: tuple[dict[str, object], ...]
    overlap_rows: tuple[dict[str, object], ...]
    pick_return_rows: tuple[dict[str, object], ...]
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
            'challenger_rule': {
                'hard_requirements': [
                    'above_sma200 == true',
                    'return_6m > 0',
                    'relative_strength_6m > 0',
                    f'average_traded_value_20 >= {MIN_ACCEPTABLE_TRADED_VALUE} when present',
                    f'drawdown_252 >= {MIN_ACCEPTABLE_DRAWDOWN} when present',
                    f'distance_to_sma200 <= {MAX_WATCH_DISTANCE_TO_SMA200} when present',
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
                'same_rebalance_dates_for_incumbent_challenger_context': True,
                'same_forward_windows_for_incumbent_challenger_context': True,
                'same_benchmark_for_incumbent_challenger_context': True,
                'same_transaction_cost_for_incumbent_challenger_context': True,
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


def build_challenger_comparison(
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
) -> ChallengerComparisonResult:
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
    overlap_rows = _overlap_rows(snapshots=snapshots, windows=normalized_windows)
    technical = _technical_validity(snapshots, rebalance_dates=rebalance_dates, windows=normalized_windows)
    comparison_summary = _comparison_summary(group_summary, monthly_breakdown, windows=normalized_windows)
    decision, reasons = recommend_next_action(
        technical=technical,
        comparison_summary=comparison_summary,
        overlap_rows=overlap_rows,
        windows=normalized_windows,
    )
    decision_rows = _decision_rows(decision=decision, reasons=reasons, comparison_summary=comparison_summary)
    return ChallengerComparisonResult(
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
        challenger_id=CHALLENGER_ID,
        incumbent_id=BASELINE_ID,
        comparison_summary=comparison_summary,
        decision_recommendation=decision,
        decision_reasons=reasons,
        group_summary_rows=group_summary,
        monthly_breakdown_rows=monthly_breakdown,
        overlap_rows=overlap_rows,
        pick_return_rows=grouped_rows,
        decision_rows=decision_rows,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_challenger_comparison_outputs(*, result: ChallengerComparisonResult, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'challenger_comparison_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'challenger_comparison_summary.md').write_text(_render_markdown(result), encoding='utf-8')
    _write_csv(path / 'challenger_group_summary.csv', result.group_summary_rows)
    _write_csv(path / 'challenger_monthly_breakdown.csv', result.monthly_breakdown_rows)
    _write_csv(path / 'challenger_overlap.csv', result.overlap_rows)
    _write_csv(path / 'challenger_pick_returns.csv', result.pick_return_rows)
    _write_csv(path / 'challenger_decision.csv', result.decision_rows)


def select_challenger_rs6m_trend_risk(rows: tuple[dict[str, object], ...], *, limit: int = 10) -> tuple[dict[str, object], ...]:
    eligible = tuple(row for row in rows if _challenger_eligible(row))
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
    overlap_rows: tuple[dict[str, object], ...],
    windows: tuple[int, ...],
) -> tuple[str, tuple[str, ...]]:
    if not technical['technical_valid']:
        return DECISION_BLOCKED_DATA, ('data_or_alignment_issue_prevents_challenger_comparison',)
    if not technical['methodology_clean']:
        return DECISION_FIX_METHODOLOGY, ('comparison_dates_or_windows_are_not_identical',)
    spreads = comparison_summary['challenger_minus_incumbent_net_excess_by_window']
    spread_60 = _as_optional_float(spreads.get('60'))
    spread_20 = _as_optional_float(spreads.get('20'))
    if spread_60 is not None and spread_60 > 0.0 and (spread_20 is None or spread_20 >= 0.0):
        return DECISION_PROMOTE, ('challenger_beats_incumbent_60d_and_is_not_worse_20d',)
    if _filter_damage_likely(overlap_rows=overlap_rows, windows=windows):
        return DECISION_FILTER_DAMAGE, ('challenger_filters_removed_positive_incumbent_only_rs_winners',)
    return DECISION_KEEP_INCUMBENT, ('challenger_did_not_beat_incumbent_baseline',)


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
        GROUP_CHALLENGER: select_challenger_rs6m_trend_risk(rows),
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


def _challenger_eligible(row: dict[str, object]) -> bool:
    return all(
        (
            bool(row.get('above_sma200')),
            _as_optional_float(row.get('return_6m')) is not None and _as_float(row['return_6m']) > 0.0,
            _as_optional_float(row.get('relative_strength_6m')) is not None and _as_float(row['relative_strength_6m']) > 0.0,
            _optional_minimum(row.get('average_traded_value_20'), MIN_ACCEPTABLE_TRADED_VALUE),
            _optional_minimum(row.get('drawdown_252'), MIN_ACCEPTABLE_DRAWDOWN),
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
    include_month: bool = False,
) -> tuple[dict[str, object], ...]:
    if include_month:
        keys = sorted({(row['rebalance_date'], row['group']) for row in grouped_rows})
    else:
        keys = sorted({row['group'] for row in grouped_rows})
    output = []
    for key in keys:
        if include_month:
            rebalance_date, group = key
            rows = tuple(row for row in grouped_rows if row['rebalance_date'] == rebalance_date and row['group'] == group)
        else:
            group = key
            rows = tuple(row for row in grouped_rows if row['group'] == group)
        for window in windows:
            row = {'group': group, 'forward_window_trading_days': window, **_aggregate_rows(rows=rows, window=window)}
            if include_month:
                row = {'rebalance_date': key[0], **row}
            output.append(row)
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
    for snapshot in snapshots:
        incumbent = snapshot.groups[GROUP_INCUMBENT]
        challenger = snapshot.groups[GROUP_CHALLENGER]
        incumbent_tickers = _ticker_set(incumbent)
        challenger_tickers = _ticker_set(challenger)
        overlap = sorted(incumbent_tickers & challenger_tickers)
        incumbent_only = sorted(incumbent_tickers - challenger_tickers)
        challenger_only = sorted(challenger_tickers - incumbent_tickers)
        for window in windows:
            output.append(
                {
                    'rebalance_date': snapshot.result.as_of_date,
                    'forward_window_trading_days': window,
                    'overlap_count': len(overlap),
                    'overlap_tickers': ';'.join(overlap),
                    'incumbent_only_tickers': ';'.join(incumbent_only),
                    'challenger_only_tickers': ';'.join(challenger_only),
                    'overlap_mean_net_excess_return': _mean_for_tickers(snapshot.rows, overlap, window),
                    'incumbent_only_mean_net_excess_return': _mean_for_tickers(snapshot.rows, incumbent_only, window),
                    'challenger_only_mean_net_excess_return': _mean_for_tickers(snapshot.rows, challenger_only, window),
                }
            )
    return tuple(output)


def _technical_validity(
    snapshots: tuple[_SnapshotContext, ...],
    *,
    rebalance_dates: tuple[date, ...],
    windows: tuple[int, ...],
) -> dict[str, object]:
    ranked_counts = [snapshot.result.snapshot.ranked_count for snapshot in snapshots]
    missing_exits = sum(len(snapshot.result.missing_exit_rows) for snapshot in snapshots)
    complete_counts = {
        str(window): sum(1 for snapshot in snapshots for row in snapshot.rows if row.get(f'{window}d_complete'))
        for window in windows
    }
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
        'complete_return_counts': complete_counts,
        'missing_forward_exit_count': missing_exits,
        'group_pick_counts': group_pick_counts,
    }


def _comparison_summary(
    group_summary_rows: tuple[dict[str, object], ...],
    monthly_rows: tuple[dict[str, object], ...],
    *,
    windows: tuple[int, ...],
) -> dict[str, object]:
    by_group = {(row['group'], int(row['forward_window_trading_days'])): row for row in group_summary_rows}
    spread_by_window: dict[str, float | None] = {}
    monthly_wins: dict[str, dict[str, int]] = {}
    for window in windows:
        challenger = by_group.get((GROUP_CHALLENGER, window))
        incumbent = by_group.get((GROUP_INCUMBENT, window))
        spread_by_window[str(window)] = _optional_difference(challenger, incumbent, 'mean_net_excess_return')
        monthly_wins[str(window)] = _monthly_win_counts(monthly_rows, window=window)
    return {
        'challenger_minus_incumbent_net_excess_by_window': spread_by_window,
        'monthly_win_loss_by_window': monthly_wins,
        'incumbent': {str(window): by_group.get((GROUP_INCUMBENT, window), {}) for window in windows},
        'challenger': {str(window): by_group.get((GROUP_CHALLENGER, window), {}) for window in windows},
        'v2_raw_top10_context': {str(window): by_group.get((GROUP_V2_RAW_TOP10, window), {}) for window in windows},
    }


def _monthly_win_counts(rows: tuple[dict[str, object], ...], *, window: int) -> dict[str, int]:
    by_key = {(row['rebalance_date'], row['group']): row for row in rows if int(row['forward_window_trading_days']) == window}
    dates = sorted({row['rebalance_date'] for row in rows if int(row['forward_window_trading_days']) == window})
    counts = {'challenger_win': 0, 'incumbent_win': 0, 'tie': 0, 'unavailable': 0}
    for rebalance_date in dates:
        challenger = by_key.get((rebalance_date, GROUP_CHALLENGER))
        incumbent = by_key.get((rebalance_date, GROUP_INCUMBENT))
        spread = _optional_difference(challenger, incumbent, 'mean_net_excess_return')
        if spread is None:
            counts['unavailable'] += 1
        elif spread > 0:
            counts['challenger_win'] += 1
        elif spread < 0:
            counts['incumbent_win'] += 1
        else:
            counts['tie'] += 1
    return counts


def _decision_rows(*, decision: str, reasons: tuple[str, ...], comparison_summary: dict[str, object]) -> tuple[dict[str, object], ...]:
    spreads = comparison_summary['challenger_minus_incumbent_net_excess_by_window']
    return (
        {
            'decision_recommendation': decision,
            'decision_reasons': '; '.join(reasons),
            'challenger_minus_incumbent_20d_net_excess': spreads.get('20'),
            'challenger_minus_incumbent_60d_net_excess': spreads.get('60'),
        },
    )


def _filter_damage_likely(*, overlap_rows: tuple[dict[str, object], ...], windows: tuple[int, ...]) -> bool:
    if 60 not in windows:
        return False
    values = [
        _as_float(row['incumbent_only_mean_net_excess_return'])
        for row in overlap_rows
        if int(row['forward_window_trading_days']) == 60 and _as_optional_float(row.get('incumbent_only_mean_net_excess_return')) is not None
    ]
    return bool(values) and _mean(values) > 0.0


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


def _render_markdown(result: ChallengerComparisonResult) -> str:
    spreads = result.comparison_summary['challenger_minus_incumbent_net_excess_by_window']
    lines = [
        '# Challenger Comparison Summary',
        '',
        f'- Decision recommendation: `{result.decision_recommendation}`',
        f'- Incumbent: `{result.incumbent_id}`',
        f'- Challenger: `{result.challenger_id}`',
        f'- Rebalance dates: `{list(result.requested_rebalance_dates)}`',
        f'- Valid snapshots: {result.technical_validity["valid_snapshot_count"]}/{result.technical_validity["rebalance_date_count"]}',
        f'- Ranked count total/range: {result.technical_validity["ranked_count_total"]} / {result.technical_validity["ranked_count_min"]}-{result.technical_validity["ranked_count_max"]}',
        f'- Complete return counts: `{result.technical_validity["complete_return_counts"]}`',
        f'- Missing exits: {result.technical_validity["missing_forward_exit_count"]}',
        f'- Challenger minus incumbent 20d net excess: `{spreads.get("20")}`',
        f'- Challenger minus incumbent 60d net excess: `{spreads.get("60")}`',
        '',
        '## Decision Reasons',
    ]
    lines.extend(f'- `{reason}`' for reason in result.decision_reasons)
    lines.extend(
        [
            '',
            '## Guardrails',
            '- Research-only diagnostic; no production ranking, policy threshold, candidate type, ML, Holdings, or Screener UI change.',
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

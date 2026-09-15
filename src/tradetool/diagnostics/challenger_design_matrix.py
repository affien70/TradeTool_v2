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
from tradetool.policy.trade_policy import MIN_ACCEPTABLE_TRADED_VALUE

CHALLENGER_RS6M_3M_TIEBREAK = 'challenger_rs6m_incumbent_plus_3m_tiebreak_v0'
CHALLENGER_RS6M_POSITIVE_3M = 'challenger_rs6m_positive_3m_rs_v0'
CHALLENGER_RS6M_LIQUIDITY = 'challenger_rs6m_liquidity_only_v0'
CHALLENGER_RS6M_TREND = 'challenger_rs6m_trend_only_no_drawdown_v0'
CHALLENGER_IDS = (
    CHALLENGER_RS6M_3M_TIEBREAK,
    CHALLENGER_RS6M_POSITIVE_3M,
    CHALLENGER_RS6M_LIQUIDITY,
    CHALLENGER_RS6M_TREND,
)
DECISION_CANDIDATE_FOUND = 'candidate_challenger_found_for_cross_universe_test'
DECISION_KEEP_INCUMBENT = 'keep_incumbent_no_candidate_challenger'
DECISION_FIX_METHODOLOGY = 'fix_challenger_design_methodology'
EXPECTED_REPORT_FILES = (
    'challenger_design_matrix_summary.md',
    'challenger_design_matrix_summary.json',
    'challenger_design_group_summary.csv',
    'challenger_design_monthly.csv',
    'challenger_design_overlap.csv',
    'challenger_design_decision.csv',
)
GROUP_INCUMBENT = BASELINE_ID
COMPARE_GROUPS = (GROUP_INCUMBENT, *CHALLENGER_IDS)
MATERIAL_20D_LAG_LIMIT = -0.002


@dataclass(frozen=True, slots=True)
class ChallengerDesignMatrixResult:
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
    challenger_ids: tuple[str, ...]
    technical_validity: dict[str, object]
    comparison_summary: dict[str, object]
    decision_recommendation: str
    best_challenger_id: str | None
    decision_reasons: tuple[str, ...]
    group_summary_rows: tuple[dict[str, object], ...]
    monthly_rows: tuple[dict[str, object], ...]
    overlap_rows: tuple[dict[str, object], ...]
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
            'challenger_ids': list(self.challenger_ids),
            'challenger_rules': _challenger_rules(),
            'technical_validity': self.technical_validity,
            'comparison_summary': self.comparison_summary,
            'decision_recommendation': self.decision_recommendation,
            'best_challenger_id': self.best_challenger_id,
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


def build_challenger_design_matrix(
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
) -> ChallengerDesignMatrixResult:
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
    technical = _technical_validity(snapshots, rebalance_dates=rebalance_dates, windows=normalized_windows)
    comparison_summary = _comparison_summary(group_summary, monthly_rows, windows=normalized_windows)
    decision, best, reasons = recommend_next_action(technical=technical, comparison_summary=comparison_summary)
    decision_rows = _decision_rows(decision=decision, best=best, reasons=reasons, comparison_summary=comparison_summary)
    return ChallengerDesignMatrixResult(
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
        challenger_ids=CHALLENGER_IDS,
        technical_validity=technical,
        comparison_summary=comparison_summary,
        decision_recommendation=decision,
        best_challenger_id=best,
        decision_reasons=reasons,
        group_summary_rows=group_summary,
        monthly_rows=monthly_rows,
        overlap_rows=overlap_rows,
        decision_rows=decision_rows,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_challenger_design_matrix_outputs(*, result: ChallengerDesignMatrixResult, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'challenger_design_matrix_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'challenger_design_matrix_summary.md').write_text(_render_markdown(result), encoding='utf-8')
    _write_csv(path / 'challenger_design_group_summary.csv', result.group_summary_rows)
    _write_csv(path / 'challenger_design_monthly.csv', result.monthly_rows)
    _write_csv(path / 'challenger_design_overlap.csv', result.overlap_rows)
    _write_csv(path / 'challenger_design_decision.csv', result.decision_rows)


def select_design_challenger(rows: tuple[dict[str, object], ...], challenger_id: str, *, limit: int = 10) -> tuple[dict[str, object], ...]:
    if challenger_id == CHALLENGER_RS6M_3M_TIEBREAK:
        eligible = tuple(row for row in rows if _has_number(row.get('relative_strength_6m')))
        key = lambda row: (-_as_float(row['relative_strength_6m']), -_safe_float(row.get('relative_strength_3m')), str(row['ticker']))
    elif challenger_id == CHALLENGER_RS6M_POSITIVE_3M:
        eligible = tuple(
            row
            for row in rows
            if _has_number(row.get('relative_strength_6m')) and _has_number(row.get('relative_strength_3m')) and _as_float(row['relative_strength_3m']) > 0.0
        )
        key = lambda row: (-_as_float(row['relative_strength_6m']), -_as_float(row['relative_strength_3m']), str(row['ticker']))
    elif challenger_id == CHALLENGER_RS6M_LIQUIDITY:
        eligible = tuple(row for row in rows if _has_number(row.get('relative_strength_6m')) and _liquidity_ok(row))
        key = lambda row: (-_as_float(row['relative_strength_6m']), str(row['ticker']))
    elif challenger_id == CHALLENGER_RS6M_TREND:
        eligible = tuple(
            row
            for row in rows
            if bool(row.get('above_sma200'))
            and _has_number(row.get('return_6m'))
            and _as_float(row['return_6m']) > 0.0
            and _has_number(row.get('relative_strength_6m'))
        )
        key = lambda row: (-_as_float(row['relative_strength_6m']), str(row['ticker']))
    else:
        raise ValueError(f'Unknown challenger ID: {challenger_id}')
    return tuple(sorted(eligible, key=key)[:limit])


def recommend_next_action(
    *,
    technical: dict[str, object],
    comparison_summary: dict[str, object],
) -> tuple[str, str | None, tuple[str, ...]]:
    if not technical['technical_valid'] or not technical['methodology_clean']:
        return DECISION_FIX_METHODOLOGY, None, ('challenger_design_dates_windows_or_data_are_inconsistent',)
    candidates = []
    spreads = comparison_summary['spreads_vs_incumbent']
    for challenger_id in CHALLENGER_IDS:
        spread_60 = _as_optional_float(spreads[challenger_id].get('60'))
        spread_20 = _as_optional_float(spreads[challenger_id].get('20'))
        if spread_60 is not None and spread_60 > 0.0 and (spread_20 is None or spread_20 >= MATERIAL_20D_LAG_LIMIT):
            candidates.append((spread_60, spread_20 if spread_20 is not None else 0.0, challenger_id))
    if candidates:
        best = max(candidates, key=lambda item: (item[0], item[1], item[2]))[2]
        return DECISION_CANDIDATE_FOUND, best, ('challenger_beats_incumbent_60d_and_is_not_materially_worse_20d',)
    return DECISION_KEEP_INCUMBENT, None, ('no_design_challenger_cleanly_beats_incumbent',)


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
    groups = {GROUP_INCUMBENT: select_incumbent_baseline(rows)}
    groups.update({challenger_id: select_design_challenger(rows, challenger_id) for challenger_id in CHALLENGER_IDS})
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


def _group_rows_for_snapshot(snapshot: _SnapshotContext) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            'rebalance_date': snapshot.result.as_of_date,
            'group': group,
            'group_role': 'incumbent' if group == GROUP_INCUMBENT else 'challenger',
            **row,
        }
        for group in COMPARE_GROUPS
        for row in snapshot.groups.get(group, ())
    )


def _summarize_grouped_rows(grouped_rows: tuple[dict[str, object], ...], *, windows: tuple[int, ...]) -> tuple[dict[str, object], ...]:
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
                output.append({'rebalance_date': rebalance_date, 'group': group, 'forward_window_trading_days': window, **_aggregate_rows(rows=rows, window=window)})
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


def _overlap_rows(*, snapshots: tuple[_SnapshotContext, ...], windows: tuple[int, ...]) -> tuple[dict[str, object], ...]:
    output = []
    for snapshot in snapshots:
        incumbent = _ticker_set(snapshot.groups[GROUP_INCUMBENT])
        for challenger_id in CHALLENGER_IDS:
            challenger = _ticker_set(snapshot.groups[challenger_id])
            overlap = sorted(incumbent & challenger)
            incumbent_only = sorted(incumbent - challenger)
            challenger_only = sorted(challenger - incumbent)
            for window in windows:
                output.append(
                    {
                        'rebalance_date': snapshot.result.as_of_date,
                        'challenger_id': challenger_id,
                        'forward_window_trading_days': window,
                        'overlap_count': len(overlap),
                        'overlap_tickers': ';'.join(overlap),
                        'incumbent_only_tickers': ';'.join(incumbent_only),
                        'challenger_only_tickers': ';'.join(challenger_only),
                        'incumbent_only_mean_net_excess_return': _mean_for_tickers(snapshot.rows, incumbent_only, window),
                        'challenger_only_mean_net_excess_return': _mean_for_tickers(snapshot.rows, challenger_only, window),
                    }
                )
    return tuple(output)


def _technical_validity(snapshots: tuple[_SnapshotContext, ...], *, rebalance_dates: tuple[date, ...], windows: tuple[int, ...]) -> dict[str, object]:
    ranked_counts = [snapshot.result.snapshot.ranked_count for snapshot in snapshots]
    missing_exits = sum(len(snapshot.result.missing_exit_rows) for snapshot in snapshots)
    group_pick_counts = {group: sum(len(snapshot.groups[group]) for snapshot in snapshots) for group in COMPARE_GROUPS}
    return {
        'technical_valid': bool(snapshots) and all(snapshot.result.snapshot.snapshot_valid for snapshot in snapshots) and missing_exits == 0,
        'methodology_clean': True,
        'rebalance_date_count': len(rebalance_dates),
        'requested_rebalance_dates': [day.isoformat() for day in rebalance_dates],
        'valid_snapshot_count': sum(1 for snapshot in snapshots if snapshot.result.snapshot.snapshot_valid),
        'ranked_count_total': sum(ranked_counts),
        'ranked_count_min': min(ranked_counts) if ranked_counts else 0,
        'ranked_count_max': max(ranked_counts) if ranked_counts else 0,
        'complete_return_counts': {str(window): sum(1 for snapshot in snapshots for row in snapshot.rows if row.get(f'{window}d_complete')) for window in windows},
        'missing_forward_exit_count': missing_exits,
        'group_pick_counts': group_pick_counts,
    }


def _comparison_summary(group_summary_rows: tuple[dict[str, object], ...], monthly_rows: tuple[dict[str, object], ...], *, windows: tuple[int, ...]) -> dict[str, object]:
    by_group = {(row['group'], int(row['forward_window_trading_days'])): row for row in group_summary_rows}
    spreads = {challenger_id: {} for challenger_id in CHALLENGER_IDS}
    monthly = {challenger_id: {} for challenger_id in CHALLENGER_IDS}
    for challenger_id in CHALLENGER_IDS:
        for window in windows:
            spreads[challenger_id][str(window)] = _optional_difference(by_group.get((challenger_id, window)), by_group.get((GROUP_INCUMBENT, window)), 'mean_net_excess_return')
            monthly[challenger_id][str(window)] = _monthly_win_counts(monthly_rows, challenger_id=challenger_id, window=window)
    return {
        'incumbent': {str(window): by_group.get((GROUP_INCUMBENT, window), {}) for window in windows},
        'challengers': {challenger_id: {str(window): by_group.get((challenger_id, window), {}) for window in windows} for challenger_id in CHALLENGER_IDS},
        'spreads_vs_incumbent': spreads,
        'monthly_win_loss_vs_incumbent_by_challenger': monthly,
    }


def _monthly_win_counts(rows: tuple[dict[str, object], ...], *, challenger_id: str, window: int) -> dict[str, int]:
    by_key = {(row['rebalance_date'], row['group']): row for row in rows if int(row['forward_window_trading_days']) == window}
    dates = sorted({row['rebalance_date'] for row in rows if int(row['forward_window_trading_days']) == window})
    counts = {'challenger_win': 0, 'incumbent_win': 0, 'tie': 0, 'unavailable': 0}
    for rebalance_date in dates:
        challenger = by_key.get((rebalance_date, challenger_id))
        incumbent = by_key.get((rebalance_date, GROUP_INCUMBENT))
        spread = _optional_difference(challenger, incumbent, 'mean_net_excess_return')
        if spread is None:
            counts['unavailable'] += 1
        elif spread > 0.0:
            counts['challenger_win'] += 1
        elif spread < 0.0:
            counts['incumbent_win'] += 1
        else:
            counts['tie'] += 1
    return counts


def _decision_rows(*, decision: str, best: str | None, reasons: tuple[str, ...], comparison_summary: dict[str, object]) -> tuple[dict[str, object], ...]:
    rows = []
    for challenger_id in CHALLENGER_IDS:
        rows.append(
            {
                'decision_recommendation': decision,
                'best_challenger_id': best,
                'challenger_id': challenger_id,
                'decision_reasons': '; '.join(reasons),
                'challenger_minus_incumbent_20d_net_excess': comparison_summary['spreads_vs_incumbent'][challenger_id].get('20'),
                'challenger_minus_incumbent_60d_net_excess': comparison_summary['spreads_vs_incumbent'][challenger_id].get('60'),
            }
        )
    return tuple(rows)


def _challenger_rules() -> dict[str, dict[str, object]]:
    return {
        CHALLENGER_RS6M_3M_TIEBREAK: {'requirements': ['feature complete'], 'sort': ['relative_strength_6m descending', 'relative_strength_3m descending', 'ticker ascending']},
        CHALLENGER_RS6M_POSITIVE_3M: {'requirements': ['relative_strength_3m > 0'], 'sort': ['relative_strength_6m descending', 'relative_strength_3m descending', 'ticker ascending']},
        CHALLENGER_RS6M_LIQUIDITY: {'requirements': [f'average_traded_value_20 >= {MIN_ACCEPTABLE_TRADED_VALUE} when present'], 'sort': ['relative_strength_6m descending', 'ticker ascending']},
        CHALLENGER_RS6M_TREND: {'requirements': ['above_sma200 == true', 'return_6m > 0'], 'sort': ['relative_strength_6m descending', 'ticker ascending']},
    }


def _liquidity_ok(row: dict[str, object]) -> bool:
    value = _as_optional_float(row.get('average_traded_value_20'))
    return value is None or value >= MIN_ACCEPTABLE_TRADED_VALUE


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


def _optional_difference(left: dict[str, object] | None, right: dict[str, object] | None, field: str) -> float | None:
    if left is None or right is None:
        return None
    left_value = _as_optional_float(left.get(field))
    right_value = _as_optional_float(right.get(field))
    if left_value is None or right_value is None:
        return None
    return left_value - right_value


def _render_markdown(result: ChallengerDesignMatrixResult) -> str:
    lines = [
        '# Challenger Design Matrix',
        '',
        f'- Decision recommendation: `{result.decision_recommendation}`',
        f'- Best challenger: `{result.best_challenger_id}`',
        f'- Incumbent: `{result.incumbent_id}`',
        f'- Universe: `{result.universe_id}`',
        f'- Benchmark: `{result.benchmark_ticker}`',
        f'- Valid snapshots: {result.technical_validity["valid_snapshot_count"]}/{result.technical_validity["rebalance_date_count"]}',
        f'- Ranked count total/range: {result.technical_validity["ranked_count_total"]} / {result.technical_validity["ranked_count_min"]}-{result.technical_validity["ranked_count_max"]}',
        '',
        '## Spreads Versus Incumbent',
    ]
    for challenger_id in CHALLENGER_IDS:
        spread = result.comparison_summary['spreads_vs_incumbent'][challenger_id]
        lines.append(f'- `{challenger_id}`: 20d `{spread.get("20")}`, 60d `{spread.get("60")}`')
    lines.extend(['', '## Decision Reasons'])
    lines.extend(f'- `{reason}`' for reason in result.decision_reasons)
    lines.extend(['', '## Guardrails', '- Research-only design matrix; no production ranking, Screener UI, policy threshold, ML, or Holdings change.'])
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
    if isinstance(value, (list, tuple, set, dict)):
        return json.dumps(value, sort_keys=True)
    return value


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _has_number(value: object) -> bool:
    return _as_optional_float(value) is not None


def _safe_float(value: object) -> float:
    parsed = _as_optional_float(value)
    return parsed if parsed is not None else float('-inf')


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

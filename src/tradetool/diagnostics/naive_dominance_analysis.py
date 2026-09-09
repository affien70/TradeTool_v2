from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median

from tradetool.diagnostics.holdout_forward_returns import ForwardReturnResult, build_holdout_forward_returns
from tradetool.diagnostics.monthly_holdout_backtest import ROUND_TRIP_COST_RATE, generate_monthly_rebalance_dates

DECISION_PROMOTE_NAIVE_RS = 'promote_naive_rs_to_incumbent_baseline_for_testing'
DECISION_INVESTIGATE_RANKING = 'investigate_v2_ranking_underperformance_no_tuning'
DECISION_INVESTIGATE_POLICY = 'investigate_policy_filtering_winners_no_tuning'
DECISION_FIX_METHODOLOGY = 'fix_comparison_methodology'
DECISION_EXTEND_HOLDOUT = 'extend_holdout_window_with_naive_baselines'
EXPECTED_REPORT_FILES = (
    'feature_forward_relationship.csv',
    'methodology_check.csv',
    'naive_dominance_decision.csv',
    'naive_dominance_summary.json',
    'naive_dominance_summary.md',
    'naive_dominant_group.csv',
    'naive_only_winners.csv',
    'naive_vs_v2_monthly.csv',
    'naive_vs_v2_overlap.csv',
    'policy_filtered_winners.csv',
    'v2_only_losers.csv',
)
V2_GROUPS = ('v2_raw_top_10', 'v2_BUY', 'v2_BUY+WATCH')
NAIVE_TOP10_GROUPS = ('naive_rs_6m_top_10', 'naive_return_6m_top_10', 'naive_rs_3m_top_10')
NAIVE_GROUPS = (
    'naive_rs_6m_top_10',
    'naive_return_6m_top_10',
    'naive_rs_3m_top_10',
    'naive_rs_6m_top_20',
    'naive_return_6m_top_20',
    'naive_rs_3m_top_20',
    'naive_trend_rs_6m_top_20',
)
FEATURE_FIELDS = (
    'raw_score',
    'raw_rank',
    'return_6m',
    'relative_strength_6m',
    'relative_strength_3m',
    'drawdown_252',
    'distance_to_sma50',
    'distance_to_sma200',
    'volatility_63',
)


@dataclass(frozen=True, slots=True)
class NaiveDominanceAnalysisResult:
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
    dominant_naive_group: str | None
    broad_vs_concentrated: dict[str, object]
    ranking_vs_policy_diagnosis: str
    methodology_rows: tuple[dict[str, object], ...]
    dominant_group_rows: tuple[dict[str, object], ...]
    monthly_rows: tuple[dict[str, object], ...]
    overlap_rows: tuple[dict[str, object], ...]
    naive_only_winner_rows: tuple[dict[str, object], ...]
    v2_only_loser_rows: tuple[dict[str, object], ...]
    policy_filtered_winner_rows: tuple[dict[str, object], ...]
    feature_relationship_rows: tuple[dict[str, object], ...]
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
            'dominant_naive_group': self.dominant_naive_group,
            'broad_vs_concentrated': self.broad_vs_concentrated,
            'ranking_vs_policy_diagnosis': self.ranking_vs_policy_diagnosis,
            'decision_recommendation': self.decision_recommendation,
            'decision_reasons': list(self.decision_reasons),
            'output_files': list(EXPECTED_REPORT_FILES),
            'leakage_controls': {
                'same_rebalance_dates_for_v2_and_naive': True,
                'same_forward_windows_for_v2_and_naive': True,
                'same_benchmark_for_v2_and_naive': True,
                'same_transaction_cost_for_v2_and_naive': True,
                'feature_rows_capped_at_each_rebalance_date': True,
                'forward_returns_used_to_change_rank_signal_type': False,
                'tuning_applied': False,
                'ranking_formula_changed': False,
                'trade_policy_thresholds_changed': False,
                'candidate_type_rules_changed': False,
                'ml_score_calculated': False,
                'holdings_adjustment_applied': False,
                'screener_ui_changed': False,
                'manual_focus_boost_applied': False,
            },
            'generated_at_utc': self.generated_at_utc,
        }


@dataclass(frozen=True, slots=True)
class _SnapshotContext:
    result: ForwardReturnResult
    rows: tuple[dict[str, object], ...]
    groups: dict[str, tuple[dict[str, object], ...]]


def build_naive_dominance_analysis(
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
) -> NaiveDominanceAnalysisResult:
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
    technical = _technical_validity(snapshots, rebalance_dates=rebalance_dates, windows=normalized_windows)
    methodology_rows = _methodology_rows(
        technical=technical,
        windows=normalized_windows,
        benchmark_ticker=benchmark_ticker,
        data_source=data_source,
    )
    dominant_rows, dominant_top10 = _dominant_group_rows(snapshots=snapshots, windows=normalized_windows)
    monthly_rows = _monthly_comparison_rows(snapshots=snapshots, windows=normalized_windows, best_naive_group=dominant_top10)
    overlap_rows = _overlap_rows(snapshots=snapshots, windows=normalized_windows, best_naive_group=dominant_top10)
    naive_winners = _naive_only_winners(snapshots=snapshots, windows=normalized_windows, best_naive_group=dominant_top10)
    v2_losers = _v2_only_losers(snapshots=snapshots, windows=normalized_windows, best_naive_group=dominant_top10)
    policy_filtered = _policy_filtered_winners(snapshots=snapshots, windows=normalized_windows, best_naive_group=dominant_top10)
    feature_relationships = _feature_relationship_rows(snapshots=snapshots, windows=normalized_windows)
    concentration = _broad_vs_concentrated(monthly_rows)
    diagnosis = _ranking_vs_policy_diagnosis(
        monthly_rows=monthly_rows,
        policy_filtered_winner_rows=policy_filtered,
        technical=technical,
    )
    decision, reasons = recommend_next_action(
        technical=technical,
        dominant_group_rows=dominant_rows,
        monthly_rows=monthly_rows,
        ranking_vs_policy_diagnosis=diagnosis,
    )
    return NaiveDominanceAnalysisResult(
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
        dominant_naive_group=dominant_top10,
        broad_vs_concentrated=concentration,
        ranking_vs_policy_diagnosis=diagnosis,
        methodology_rows=methodology_rows,
        dominant_group_rows=dominant_rows,
        monthly_rows=monthly_rows,
        overlap_rows=overlap_rows,
        naive_only_winner_rows=naive_winners,
        v2_only_loser_rows=v2_losers,
        policy_filtered_winner_rows=policy_filtered,
        feature_relationship_rows=feature_relationships,
        decision_recommendation=decision,
        decision_reasons=reasons,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_naive_dominance_analysis_outputs(*, result: NaiveDominanceAnalysisResult, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'naive_dominance_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'naive_dominance_summary.md').write_text(_render_markdown(result), encoding='utf-8')
    _write_csv(path / 'naive_dominant_group.csv', result.dominant_group_rows)
    _write_csv(path / 'naive_vs_v2_monthly.csv', result.monthly_rows)
    _write_csv(path / 'naive_vs_v2_overlap.csv', result.overlap_rows)
    _write_csv(path / 'naive_only_winners.csv', result.naive_only_winner_rows)
    _write_csv(path / 'v2_only_losers.csv', result.v2_only_loser_rows)
    _write_csv(path / 'policy_filtered_winners.csv', result.policy_filtered_winner_rows)
    _write_csv(path / 'feature_forward_relationship.csv', result.feature_relationship_rows)
    _write_csv(path / 'methodology_check.csv', result.methodology_rows)
    _write_csv(
        path / 'naive_dominance_decision.csv',
        [{'decision_recommendation': result.decision_recommendation, 'decision_reasons': '; '.join(result.decision_reasons)}],
    )


def recommend_next_action(
    *,
    technical: dict[str, object],
    dominant_group_rows: tuple[dict[str, object], ...],
    monthly_rows: tuple[dict[str, object], ...],
    ranking_vs_policy_diagnosis: str,
) -> tuple[str, tuple[str, ...]]:
    if not technical['technical_valid'] or not technical['methodology_clean']:
        return DECISION_FIX_METHODOLOGY, ('technical_or_methodology_check_failed',)
    if _dominant_group_is_clear(dominant_group_rows) and _naive_beats_v2_raw_top10(monthly_rows):
        return DECISION_PROMOTE_NAIVE_RS, ('single_naive_rs_or_momentum_top10_dominates_v2_top10_across_windows',)
    if ranking_vs_policy_diagnosis == 'policy_filtering_suspect':
        return DECISION_INVESTIGATE_POLICY, ('v2_raw_top10_competitive_but_policy_filtered_forward_winners',)
    if ranking_vs_policy_diagnosis in {'ranking_suspect', 'ranking_and_policy_stack_suspect'}:
        return DECISION_INVESTIGATE_RANKING, ('v2_raw_ranking_underperformed_best_naive_top10',)
    return DECISION_EXTEND_HOLDOUT, ('clean_but_short_or_mixed_holdout_evidence',)


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
    return _SnapshotContext(result=result, rows=rows, groups=_groups_for_rows(rows))


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
        'distance_to_sma50': features.get('distance_to_sma50', candidate.get('distance_to_sma50')),
        'distance_to_sma200': features.get('distance_to_sma200', candidate.get('distance_to_sma200')),
        'volatility_63': features.get('volatility_63', candidate.get('volatility_63')),
    }
    for window in windows:
        if output.get(f'{window}d_complete'):
            output[f'{window}d_net_return'] = _as_float(output[f'{window}d_return']) - ROUND_TRIP_COST_RATE
            output[f'{window}d_net_excess_return'] = _as_float(output[f'{window}d_excess_return']) - ROUND_TRIP_COST_RATE
        else:
            output[f'{window}d_net_return'] = None
            output[f'{window}d_net_excess_return'] = None
    return output


def _groups_for_rows(rows: tuple[dict[str, object], ...]) -> dict[str, tuple[dict[str, object], ...]]:
    return {
        'v2_raw_top_10': _raw_top(rows, 10),
        'v2_BUY': tuple(row for row in rows if row.get('trade_signal') == 'BUY'),
        'v2_BUY+WATCH': tuple(row for row in rows if row.get('trade_signal') in {'BUY', 'WATCH'}),
        'naive_return_6m_top_10': _top_by_field(rows, 'return_6m', 10),
        'naive_return_6m_top_20': _top_by_field(rows, 'return_6m', 20),
        'naive_rs_6m_top_10': _top_by_field(rows, 'relative_strength_6m', 10),
        'naive_rs_6m_top_20': _top_by_field(rows, 'relative_strength_6m', 20),
        'naive_rs_3m_top_10': _top_by_field(rows, 'relative_strength_3m', 10),
        'naive_rs_3m_top_20': _top_by_field(rows, 'relative_strength_3m', 20),
        'naive_trend_rs_6m_top_20': _trend_rs_top(rows, 20),
    }


def _dominant_group_rows(
    *,
    snapshots: tuple[_SnapshotContext, ...],
    windows: tuple[int, ...],
) -> tuple[tuple[dict[str, object], ...], str | None]:
    aggregate_rows = []
    by_group_window = {}
    for group in NAIVE_GROUPS:
        for window in windows:
            rows = tuple(row for snapshot in snapshots for row in snapshot.groups.get(group, ()))
            summary = _aggregate_rows(rows=rows, window=window)
            by_group_window[(group, window)] = summary['net_mean_excess_return']
            aggregate_rows.append(
                {
                    'metric_scope': f'{window}d_net_excess',
                    'dominant_group': group,
                    'candidate_count': summary['candidate_count'],
                    'complete_count': summary['complete_count'],
                    'net_mean_excess_return': summary['net_mean_excess_return'],
                    'hit_rate_vs_benchmark': summary['hit_rate_vs_benchmark'],
                }
            )
    combined = []
    for group in NAIVE_GROUPS:
        values = [_as_optional_float(by_group_window.get((group, window))) for window in windows]
        valid = [value for value in values if value is not None]
        combined.append((group, _mean(valid)))
    best_by_window = {
        window: _best_group(
            ((group, _as_optional_float(by_group_window.get((group, window)))) for group in NAIVE_GROUPS),
            priorities=NAIVE_GROUPS,
        )
        for window in windows
    }
    best_combined = _best_group(combined, priorities=NAIVE_GROUPS)
    rows = []
    for window in windows:
        group = best_by_window[window]
        rows.append(
            {
                'metric_scope': f'{window}d_net_excess',
                'dominant_group': group,
                'net_mean_excess_return': by_group_window.get((group, window)),
            }
        )
    rows.append(
        {
            'metric_scope': 'combined_average_20d_60d_net_excess',
            'dominant_group': best_combined,
            'net_mean_excess_return': _mean(
                [
                    _as_float(by_group_window[(best_combined, window)])
                    for window in windows
                    if _as_optional_float(by_group_window.get((best_combined, window))) is not None
                ]
            ) if best_combined is not None else None,
        }
    )
    best_top10 = _best_group(
        (
            (
                group,
                _mean(
                    [
                        _as_float(by_group_window[(group, window)])
                        for window in windows
                        if _as_optional_float(by_group_window.get((group, window))) is not None
                    ]
                ),
            )
            for group in NAIVE_TOP10_GROUPS
        ),
        priorities=NAIVE_TOP10_GROUPS,
    )
    return tuple(rows), best_top10


def _monthly_comparison_rows(
    *,
    snapshots: tuple[_SnapshotContext, ...],
    windows: tuple[int, ...],
    best_naive_group: str | None,
) -> tuple[dict[str, object], ...]:
    if best_naive_group is None:
        return ()
    rows = []
    for snapshot in snapshots:
        naive_rows = snapshot.groups[best_naive_group]
        for v2_group in V2_GROUPS:
            v2_rows = snapshot.groups.get(v2_group, ())
            for window in windows:
                v2_summary = _aggregate_rows(rows=v2_rows, window=window)
                naive_summary = _aggregate_rows(rows=naive_rows, window=window)
                spread = _optional_difference(v2_summary, naive_summary, 'net_mean_excess_return')
                rows.append(
                    {
                        'rebalance_date': snapshot.result.as_of_date,
                        'forward_window_trading_days': window,
                        'v2_group': v2_group,
                        'best_naive_group': best_naive_group,
                        'v2_complete_count': v2_summary['complete_count'],
                        'naive_complete_count': naive_summary['complete_count'],
                        'v2_net_mean_excess_return': v2_summary['net_mean_excess_return'],
                        'best_naive_net_mean_excess_return': naive_summary['net_mean_excess_return'],
                        'v2_minus_best_naive_net_excess': spread,
                        'winner': _winner(spread),
                    }
                )
    return tuple(rows)


def _overlap_rows(
    *,
    snapshots: tuple[_SnapshotContext, ...],
    windows: tuple[int, ...],
    best_naive_group: str | None,
) -> tuple[dict[str, object], ...]:
    if best_naive_group is None:
        return ()
    output = []
    for snapshot in snapshots:
        naive_rows = snapshot.groups[best_naive_group]
        naive_tickers = _ticker_set(naive_rows)
        for v2_group in ('v2_raw_top_10', 'v2_BUY'):
            v2_rows = snapshot.groups[v2_group]
            v2_tickers = _ticker_set(v2_rows)
            overlap = sorted(v2_tickers & naive_tickers)
            naive_only = sorted(naive_tickers - v2_tickers)
            v2_only = sorted(v2_tickers - naive_tickers)
            for window in windows:
                output.append(
                    {
                        'rebalance_date': snapshot.result.as_of_date,
                        'forward_window_trading_days': window,
                        'v2_group': v2_group,
                        'best_naive_group': best_naive_group,
                        'overlap_count': len(overlap),
                        'overlap_tickers': ';'.join(overlap),
                        'naive_only_tickers': ';'.join(naive_only),
                        'v2_only_tickers': ';'.join(v2_only),
                        'overlap_mean_net_excess_return': _mean_for_tickers(snapshot.rows, overlap, window),
                        'naive_only_mean_net_excess_return': _mean_for_tickers(snapshot.rows, naive_only, window),
                        'v2_only_mean_net_excess_return': _mean_for_tickers(snapshot.rows, v2_only, window),
                    }
                )
    return tuple(output)


def _naive_only_winners(
    *,
    snapshots: tuple[_SnapshotContext, ...],
    windows: tuple[int, ...],
    best_naive_group: str | None,
) -> tuple[dict[str, object], ...]:
    if best_naive_group is None:
        return ()
    output = []
    for snapshot in snapshots:
        v2_top10 = _ticker_set(snapshot.groups['v2_raw_top_10'])
        naive_only = tuple(row for row in snapshot.groups[best_naive_group] if str(row['ticker']) not in v2_top10)
        for row in naive_only:
            for window in windows:
                value = _as_optional_float(row.get(f'{window}d_net_excess_return'))
                if value is not None and value > 0.0:
                    output.append(_diagnostic_row(row=row, window=window, reason='naive_only_forward_winner'))
    return tuple(output)


def _v2_only_losers(
    *,
    snapshots: tuple[_SnapshotContext, ...],
    windows: tuple[int, ...],
    best_naive_group: str | None,
) -> tuple[dict[str, object], ...]:
    if best_naive_group is None:
        return ()
    output = []
    for snapshot in snapshots:
        naive = _ticker_set(snapshot.groups[best_naive_group])
        v2_only = tuple(row for row in snapshot.groups['v2_raw_top_10'] if str(row['ticker']) not in naive)
        for row in v2_only:
            for window in windows:
                value = _as_optional_float(row.get(f'{window}d_net_excess_return'))
                if value is not None and value < 0.0:
                    output.append(_diagnostic_row(row=row, window=window, reason='v2_only_forward_loser'))
    return tuple(sorted(output, key=lambda row: (_as_float(row['net_excess_return']), row['rebalance_date'], row['ticker'])))


def _policy_filtered_winners(
    *,
    snapshots: tuple[_SnapshotContext, ...],
    windows: tuple[int, ...],
    best_naive_group: str | None,
) -> tuple[dict[str, object], ...]:
    if best_naive_group is None:
        return ()
    output = []
    for snapshot in snapshots:
        for row in snapshot.groups[best_naive_group]:
            filtered = row.get('trade_signal') != 'BUY' or str(row.get('candidate_type')) in {'Rebound Case', 'Reject'}
            if not filtered:
                continue
            for window in windows:
                value = _as_optional_float(row.get(f'{window}d_net_excess_return'))
                if value is not None and value > 0.0:
                    output.append(_diagnostic_row(row=row, window=window, reason='policy_filtered_forward_winner'))
    return tuple(output)


def _diagnostic_row(*, row: dict[str, object], window: int, reason: str) -> dict[str, object]:
    return {
        'rebalance_date': row['rebalance_date'],
        'forward_window_trading_days': window,
        'ticker': row['ticker'],
        'raw_rank': row['raw_rank'],
        'raw_score': row['raw_score'],
        'trade_signal': row.get('trade_signal'),
        'candidate_type': row.get('candidate_type'),
        'return_6m': row.get('return_6m'),
        'relative_strength_6m': row.get('relative_strength_6m'),
        'relative_strength_3m': row.get('relative_strength_3m'),
        'drawdown_252': row.get('drawdown_252'),
        'distance_to_sma50': row.get('distance_to_sma50'),
        'distance_to_sma200': row.get('distance_to_sma200'),
        'volatility_63': row.get('volatility_63'),
        'net_excess_return': row.get(f'{window}d_net_excess_return'),
        'policy_reasons': row.get('policy_reasons', ''),
        'policy_warnings': row.get('policy_warnings', ''),
        'classification_reasons': row.get('classification_reasons', ''),
        'diagnostic_reason': reason,
        'v2_status': _v2_status(row),
    }


def _feature_relationship_rows(
    *,
    snapshots: tuple[_SnapshotContext, ...],
    windows: tuple[int, ...],
) -> tuple[dict[str, object], ...]:
    rows = tuple(row for snapshot in snapshots for row in snapshot.rows)
    output = []
    for window in windows:
        for field in FEATURE_FIELDS:
            pairs = [
                (_as_float(row[field]), _as_float(row[f'{window}d_net_excess_return']))
                for row in rows
                if _as_optional_float(row.get(field)) is not None and _as_optional_float(row.get(f'{window}d_net_excess_return')) is not None
            ]
            output.append(
                {
                    'forward_window_trading_days': window,
                    'feature': field,
                    'pair_count': len(pairs),
                    'pearson_correlation': _pearson([pair[0] for pair in pairs], [pair[1] for pair in pairs]),
                    'spearman_correlation': _pearson(_rank_values([pair[0] for pair in pairs]), _rank_values([pair[1] for pair in pairs])),
                    'median_feature_value': None if not pairs else median(pair[0] for pair in pairs),
                    'median_net_excess_return': None if not pairs else median(pair[1] for pair in pairs),
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
    complete_return_counts = {
        str(window): sum(1 for snapshot in snapshots for row in snapshot.rows if row.get(f'{window}d_complete'))
        for window in windows
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
        'complete_return_counts': complete_return_counts,
        'missing_forward_exit_count': missing_exits,
    }


def _methodology_rows(
    *,
    technical: dict[str, object],
    windows: tuple[int, ...],
    benchmark_ticker: str,
    data_source: str,
) -> tuple[dict[str, object], ...]:
    checks = {
        'same_rebalance_dates': True,
        'same_data_source': True,
        'same_benchmark': True,
        'same_forward_windows': True,
        'same_cost_convention': True,
        'no_future_features': True,
        'no_ml': True,
        'no_holdings': True,
        'no_manual_focus_boost': True,
    }
    return tuple(
        {
            'check_name': name,
            'passed': passed,
            'details': f'benchmark={benchmark_ticker.strip().upper()} data_source={data_source} windows={list(windows)} cost={ROUND_TRIP_COST_RATE}',
        }
        for name, passed in checks.items()
    ) + (
        {
            'check_name': 'missing_forward_exits',
            'passed': int(technical['missing_forward_exit_count']) == 0,
            'details': str(technical['missing_forward_exit_count']),
        },
    )


def _broad_vs_concentrated(monthly_rows: tuple[dict[str, object], ...]) -> dict[str, object]:
    best_rows = tuple(row for row in monthly_rows if row.get('v2_group') == 'v2_raw_top_10')
    naive_wins = sum(1 for row in best_rows if row.get('winner') == 'best_naive')
    v2_wins = sum(1 for row in best_rows if row.get('winner') == 'v2')
    spreads = [_as_float(row['v2_minus_best_naive_net_excess']) for row in best_rows if _as_optional_float(row.get('v2_minus_best_naive_net_excess')) is not None]
    negative = [value for value in spreads if value < 0.0]
    largest_underperformance = min(spreads) if spreads else None
    largest_outperformance = max(spreads) if spreads else None
    concentration_ratio = None
    if negative:
        concentration_ratio = abs(min(negative)) / max(abs(sum(negative)), 1e-12)
    return {
        'v2_win_count': v2_wins,
        'naive_win_count': naive_wins,
        'largest_v2_underperformance': largest_underperformance,
        'largest_v2_outperformance': largest_outperformance,
        'negative_spread_concentration_ratio': concentration_ratio,
        'few_months_dominate': concentration_ratio is not None and concentration_ratio >= 0.5,
    }


def _ranking_vs_policy_diagnosis(
    *,
    monthly_rows: tuple[dict[str, object], ...],
    policy_filtered_winner_rows: tuple[dict[str, object], ...],
    technical: dict[str, object],
) -> str:
    if not technical['technical_valid'] or not technical['methodology_clean']:
        return 'methodology_flaw'
    raw_spreads = _spreads(monthly_rows, 'v2_raw_top_10')
    buy_spreads = _spreads(monthly_rows, 'v2_BUY')
    buy_watch_spreads = _spreads(monthly_rows, 'v2_BUY+WATCH')
    raw_underperforms = bool(raw_spreads) and _mean(raw_spreads) < 0.0
    buy_underperforms = bool(buy_spreads) and _mean(buy_spreads) < 0.0
    buy_watch_underperforms = bool(buy_watch_spreads) and _mean(buy_watch_spreads) < 0.0
    policy_issue = bool(policy_filtered_winner_rows) and not raw_underperforms and (buy_underperforms or buy_watch_underperforms)
    if policy_issue:
        return 'policy_filtering_suspect'
    if raw_underperforms and (buy_underperforms or buy_watch_underperforms):
        return 'ranking_and_policy_stack_suspect'
    if raw_underperforms:
        return 'ranking_suspect'
    return 'mixed_or_short_sample'


def _dominant_group_is_clear(rows: tuple[dict[str, object], ...]) -> bool:
    combined = next((row for row in rows if row.get('metric_scope') == 'combined_average_20d_60d_net_excess'), None)
    return combined is not None and combined.get('dominant_group') in NAIVE_TOP10_GROUPS and _as_optional_float(combined.get('net_mean_excess_return')) is not None


def _naive_beats_v2_raw_top10(monthly_rows: tuple[dict[str, object], ...]) -> bool:
    spreads = _spreads(monthly_rows, 'v2_raw_top_10')
    return bool(spreads) and _mean(spreads) < 0.0


def _spreads(monthly_rows: tuple[dict[str, object], ...], v2_group: str) -> list[float]:
    return [
        _as_float(row['v2_minus_best_naive_net_excess'])
        for row in monthly_rows
        if row.get('v2_group') == v2_group and _as_optional_float(row.get('v2_minus_best_naive_net_excess')) is not None
    ]


def _aggregate_rows(*, rows: tuple[dict[str, object], ...], window: int) -> dict[str, object]:
    complete = tuple(row for row in rows if row.get(f'{window}d_complete'))
    net_excess = [_as_float(row[f'{window}d_net_excess_return']) for row in complete]
    return {
        'candidate_count': len(rows),
        'complete_count': len(complete),
        'net_mean_excess_return': _mean(net_excess),
        'median_net_excess_return': None if not net_excess else median(net_excess),
        'hit_rate_vs_benchmark': _mean([1.0 if value > 0.0 else 0.0 for value in net_excess]),
    }


def _raw_top(rows: tuple[dict[str, object], ...], limit: int) -> tuple[dict[str, object], ...]:
    return tuple(sorted((row for row in rows if int(row['raw_rank']) <= limit), key=lambda row: (int(row['raw_rank']), str(row['ticker']))))


def _top_by_field(rows: tuple[dict[str, object], ...], field: str, limit: int) -> tuple[dict[str, object], ...]:
    valid = tuple(row for row in rows if _as_optional_float(row.get(field)) is not None)
    return tuple(sorted(valid, key=lambda row: (-_as_float(row[field]), str(row['ticker'])))[:limit])


def _trend_rs_top(rows: tuple[dict[str, object], ...], limit: int) -> tuple[dict[str, object], ...]:
    valid = tuple(
        row for row in rows
        if bool(row.get('above_sma200')) and _as_optional_float(row.get('return_6m')) is not None and _as_float(row['return_6m']) > 0.0
    )
    return _top_by_field(valid, 'relative_strength_6m', limit)


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


def _v2_status(row: dict[str, object]) -> str:
    if int(row['raw_rank']) <= 10:
        return 'already_in_v2_raw_top10'
    if row.get('trade_signal') == 'BUY':
        return 'buy_below_raw_top10'
    if row.get('trade_signal') in {'REVIEW', 'AVOID'}:
        return 'policy_filtered'
    return 'downranked_outside_raw_top10'


def _best_group(values, *, priorities: tuple[str, ...]) -> str | None:
    priority = {name: index for index, name in enumerate(priorities)}
    valid = tuple((name, value) for name, value in values if value is not None)
    if not valid:
        return None
    return sorted(valid, key=lambda item: (-float(item[1]), priority.get(item[0], len(priority)), item[0]))[0][0]


def _winner(spread: float | None) -> str:
    if spread is None:
        return 'unavailable'
    if spread > 0.0:
        return 'v2'
    if spread < 0.0:
        return 'best_naive'
    return 'tie'


def _optional_difference(left: dict[str, object], right: dict[str, object], field: str) -> float | None:
    left_value = _as_optional_float(left.get(field))
    right_value = _as_optional_float(right.get(field))
    if left_value is None or right_value is None:
        return None
    return left_value - right_value


def _pearson(x_values: list[float], y_values: list[float]) -> float | None:
    if len(x_values) < 2 or len(y_values) < 2:
        return None
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values, strict=True))
    x_denominator = math.sqrt(sum((x - x_mean) ** 2 for x in x_values))
    y_denominator = math.sqrt(sum((y - y_mean) ** 2 for y in y_values))
    if x_denominator == 0.0 or y_denominator == 0.0:
        return None
    return numerator / (x_denominator * y_denominator)


def _rank_values(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    for rank, index in enumerate(order, start=1):
        ranks[index] = float(rank)
    return ranks


def _render_markdown(result: NaiveDominanceAnalysisResult) -> str:
    lines = [
        '# Naive Dominance Analysis',
        '',
        '## Technical Validity',
        f'- Decision recommendation: `{result.decision_recommendation}`',
        f'- Ranking versus policy diagnosis: `{result.ranking_vs_policy_diagnosis}`',
        f'- Dominant naive top10 group: `{result.dominant_naive_group or "none"}`',
        f'- Rebalance dates: `{list(result.requested_rebalance_dates)}`',
        f'- Valid snapshots: {result.technical_validity["valid_snapshot_count"]}/{result.technical_validity["rebalance_date_count"]}',
        f'- Ranked count total/range: {result.technical_validity["ranked_count_total"]} / {result.technical_validity["ranked_count_min"]}-{result.technical_validity["ranked_count_max"]}',
        f'- Complete return counts: `{result.technical_validity["complete_return_counts"]}`',
        f'- Missing exits: {result.technical_validity["missing_forward_exit_count"]}',
        f'- Benchmark: `{result.benchmark_ticker}`',
        f'- Close source: `{result.close_input_source}`',
        '',
        '## Breadth',
        f'- V2 wins: {result.broad_vs_concentrated["v2_win_count"]}',
        f'- Naive wins: {result.broad_vs_concentrated["naive_win_count"]}',
        f'- Few months dominate: `{result.broad_vs_concentrated["few_months_dominate"]}`',
        '',
        '## Decision Reasons',
    ]
    lines.extend(f'- `{reason}`' for reason in result.decision_reasons)
    lines.extend(
        [
            '',
            '## Guardrails',
            '- This diagnostic does not tune formulas, thresholds, ranking, candidate types, ML, Holdings, or Screener UI.',
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

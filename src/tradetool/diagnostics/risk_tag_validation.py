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
from tradetool.explanation.incumbent_risk_tags import build_incumbent_risk_tags

DECISION_KEEP_INFORMATIONAL = 'keep_risk_tags_informational'
DECISION_INVESTIGATE_TAG_FILTER = 'investigate_specific_risk_tag_filter'
DECISION_DO_NOT_FILTER = 'do_not_use_risk_tags_for_filtering'
DECISION_FIX_METHODOLOGY = 'fix_risk_tag_validation_methodology'
GROUP_SELECTED_TOP10 = 'selected_top10'
GROUP_FEATURE_COMPLETE_CONTEXT = 'feature_complete_context'
TAG_NONE = 'no_risk_tag'
EXPECTED_REPORT_FILES = (
    'risk_tag_by_level.csv',
    'risk_tag_by_tag.csv',
    'risk_tag_decision.csv',
    'risk_tag_monthly.csv',
    'risk_tag_validation_summary.json',
    'risk_tag_validation_summary.md',
)


@dataclass(frozen=True, slots=True)
class RiskTagValidationResult:
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
    technical_validity: dict[str, object]
    risk_summary: dict[str, object]
    decision_recommendation: str
    decision_reasons: tuple[str, ...]
    level_rows: tuple[dict[str, object], ...]
    tag_rows: tuple[dict[str, object], ...]
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
            'technical_validity': self.technical_validity,
            'risk_summary': self.risk_summary,
            'decision_recommendation': self.decision_recommendation,
            'decision_reasons': list(self.decision_reasons),
            'output_files': list(EXPECTED_REPORT_FILES),
            'leakage_controls': {
                'same_rebalance_dates_for_all_groups': True,
                'same_forward_windows_for_all_groups': True,
                'same_benchmark_for_all_groups': True,
                'same_transaction_cost_for_all_groups': True,
                'feature_rows_capped_at_each_rebalance_date': True,
                'risk_tags_used_to_change_selection': False,
                'selection_rule_changed': False,
                'production_ranking_changed': False,
                'screener_ui_changed': False,
                'ml_or_holdings_logic_changed': False,
            },
            'generated_at_utc': self.generated_at_utc,
        }


@dataclass(frozen=True, slots=True)
class _SnapshotContext:
    result: ForwardReturnResult
    selected_rows: tuple[dict[str, object], ...]
    context_rows: tuple[dict[str, object], ...]


def build_risk_tag_validation(
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
) -> RiskTagValidationResult:
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
    selected_rows = tuple(row for snapshot in snapshots for row in snapshot.selected_rows)
    context_rows = tuple(row for snapshot in snapshots for row in snapshot.context_rows)
    scoped_rows = (
        *({**row, 'scope': GROUP_SELECTED_TOP10} for row in selected_rows),
        *({**row, 'scope': GROUP_FEATURE_COMPLETE_CONTEXT} for row in context_rows),
    )
    level_rows = _level_rows(scoped_rows, windows=normalized_windows)
    tag_rows = _tag_rows(scoped_rows, windows=normalized_windows)
    monthly_rows = _monthly_rows(selected_rows, windows=normalized_windows)
    technical = _technical_validity(snapshots, rebalance_dates=rebalance_dates, windows=normalized_windows)
    risk_summary = _risk_summary(level_rows, tag_rows, windows=normalized_windows)
    decision, reasons = recommend_next_action(technical=technical, risk_summary=risk_summary)
    decision_rows = _decision_rows(decision=decision, reasons=reasons, risk_summary=risk_summary)
    return RiskTagValidationResult(
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
        technical_validity=technical,
        risk_summary=risk_summary,
        decision_recommendation=decision,
        decision_reasons=reasons,
        level_rows=level_rows,
        tag_rows=tag_rows,
        monthly_rows=monthly_rows,
        decision_rows=decision_rows,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_risk_tag_validation_outputs(*, result: RiskTagValidationResult, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'risk_tag_validation_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'risk_tag_validation_summary.md').write_text(_render_markdown(result), encoding='utf-8')
    _write_csv(path / 'risk_tag_by_level.csv', result.level_rows)
    _write_csv(path / 'risk_tag_by_tag.csv', result.tag_rows)
    _write_csv(path / 'risk_tag_monthly.csv', result.monthly_rows)
    _write_csv(path / 'risk_tag_decision.csv', result.decision_rows)


def recommend_next_action(*, technical: dict[str, object], risk_summary: dict[str, object]) -> tuple[str, tuple[str, ...]]:
    if not technical['technical_valid'] or not technical['methodology_clean']:
        return DECISION_FIX_METHODOLOGY, ('risk_tag_validation_dates_windows_or_data_are_inconsistent',)
    comparison = risk_summary.get('high_vs_non_high_selected_top10', {})
    high_minus_non_high_20 = _as_optional_float(comparison.get('20'))
    high_minus_non_high_60 = _as_optional_float(comparison.get('60'))
    harmful_tags = tuple(risk_summary.get('harmful_tags_60d', ()))
    if high_minus_non_high_20 is not None and high_minus_non_high_60 is not None:
        if high_minus_non_high_20 > 0.02 and high_minus_non_high_60 > 0.02:
            return DECISION_DO_NOT_FILTER, ('high_risk_level_did_not_underperform_and_was_anti_predictive',)
        if high_minus_non_high_20 < -0.02 and high_minus_non_high_60 < -0.02:
            return DECISION_INVESTIGATE_TAG_FILTER, ('high_risk_level_underperformed_low_medium_selected_top10',)
    if harmful_tags:
        return DECISION_INVESTIGATE_TAG_FILTER, ('specific_risk_tags_showed_negative_60d_net_excess',)
    return DECISION_KEEP_INFORMATIONAL, ('risk_tags_do_not_yet_justify_filtering_or_demotion',)


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
        _enriched_row(
            rebalance_date=result.as_of_date,
            candidate=row,
            features=features_by_ticker.get(str(row['ticker']), {}),
            windows=windows,
        )
        for row in result.candidate_rows
    )
    selected = tuple(_mark_selected(row) for row in select_incumbent_baseline(rows))
    return _SnapshotContext(result=result, selected_rows=selected, context_rows=rows)


def _enriched_row(
    *,
    rebalance_date: str,
    candidate: dict[str, object],
    features: dict[str, object],
    windows: tuple[int, ...],
) -> dict[str, object]:
    output = {
        'rebalance_date': rebalance_date,
        **candidate,
        'return_3m': features.get('return_3m', candidate.get('return_3m')),
        'return_6m': features.get('return_6m', candidate.get('return_6m')),
        'relative_strength_3m': features.get('relative_strength_3m', candidate.get('relative_strength_3m')),
        'relative_strength_6m': features.get('relative_strength_6m', candidate.get('relative_strength_6m')),
        'above_sma200': features.get('above_sma200', candidate.get('above_sma200')),
        'average_traded_value_20': features.get('average_traded_value_20', candidate.get('average_traded_value_20')),
        'drawdown_252': features.get('drawdown_252', candidate.get('drawdown_252')),
        'volatility_63': features.get('volatility_63', candidate.get('volatility_63')),
        'distance_to_sma200': features.get('distance_to_sma200', candidate.get('distance_to_sma200')),
    }
    risk = build_incumbent_risk_tags(output)
    output['risk_level'] = risk.risk_level
    output['risk_tags'] = '|'.join(risk.risk_tags)
    output['risk_explanation_no'] = risk.risk_explanation_no
    for window in windows:
        if output.get(f'{window}d_complete'):
            output[f'{window}d_net_return'] = _as_float(output[f'{window}d_return']) - ROUND_TRIP_COST_RATE
            output[f'{window}d_net_excess_return'] = _as_float(output[f'{window}d_excess_return']) - ROUND_TRIP_COST_RATE
        else:
            output[f'{window}d_net_return'] = None
            output[f'{window}d_net_excess_return'] = None
    return output


def _mark_selected(row: dict[str, object]) -> dict[str, object]:
    return {**row, 'incumbent_selected': True}


def _level_rows(rows: tuple[dict[str, object], ...], *, windows: tuple[int, ...]) -> tuple[dict[str, object], ...]:
    output = []
    for scope in (GROUP_SELECTED_TOP10, GROUP_FEATURE_COMPLETE_CONTEXT):
        scoped = tuple(row for row in rows if row['scope'] == scope)
        for level in ('LOW', 'MEDIUM', 'HIGH'):
            level_rows = tuple(row for row in scoped if row.get('risk_level') == level)
            for window in windows:
                output.append({'scope': scope, 'risk_level': level, 'forward_window_trading_days': window, **_aggregate_rows(level_rows, window=window)})
    return tuple(output)


def _tag_rows(rows: tuple[dict[str, object], ...], *, windows: tuple[int, ...]) -> tuple[dict[str, object], ...]:
    output = []
    all_tags = sorted({tag for row in rows for tag in _split_tags(row.get('risk_tags'))} | {TAG_NONE})
    for scope in (GROUP_SELECTED_TOP10, GROUP_FEATURE_COMPLETE_CONTEXT):
        scoped = tuple(row for row in rows if row['scope'] == scope)
        for tag in all_tags:
            tagged_rows = tuple(row for row in scoped if tag in _split_tags(row.get('risk_tags')))
            for window in windows:
                output.append({'scope': scope, 'risk_tag': tag, 'forward_window_trading_days': window, **_aggregate_rows(tagged_rows, window=window)})
    return tuple(output)


def _monthly_rows(rows: tuple[dict[str, object], ...], *, windows: tuple[int, ...]) -> tuple[dict[str, object], ...]:
    output = []
    for rebalance_date in sorted({row['rebalance_date'] for row in rows}):
        date_rows = tuple(row for row in rows if row['rebalance_date'] == rebalance_date)
        for level in ('LOW', 'MEDIUM', 'HIGH'):
            level_rows = tuple(row for row in date_rows if row.get('risk_level') == level)
            for window in windows:
                output.append({'rebalance_date': rebalance_date, 'risk_level': level, 'forward_window_trading_days': window, **_aggregate_rows(level_rows, window=window)})
    return tuple(output)


def _aggregate_rows(rows: tuple[dict[str, object], ...], *, window: int) -> dict[str, object]:
    complete = tuple(row for row in rows if row.get(f'{window}d_complete'))
    returns = [_as_float(row[f'{window}d_return']) for row in complete]
    net_returns = [_as_float(row[f'{window}d_net_return']) for row in complete]
    excess = [_as_float(row[f'{window}d_excess_return']) for row in complete]
    net_excess = [_as_float(row[f'{window}d_net_excess_return']) for row in complete]
    return {
        'pick_count': len(rows),
        'complete_count': len(complete),
        'mean_return': _mean(returns),
        'median_return': None if not returns else median(returns),
        'mean_net_return': _mean(net_returns),
        'median_net_return': None if not net_returns else median(net_returns),
        'mean_excess_return': _mean(excess),
        'median_excess_return': None if not excess else median(excess),
        'mean_net_excess_return': _mean(net_excess),
        'median_net_excess_return': None if not net_excess else median(net_excess),
        'hit_rate_vs_benchmark': _mean([1.0 if value > 0.0 else 0.0 for value in excess]),
        'positive_return_rate': _mean([1.0 if value > 0.0 else 0.0 for value in returns]),
    }


def _technical_validity(snapshots: tuple[_SnapshotContext, ...], *, rebalance_dates: tuple[date, ...], windows: tuple[int, ...]) -> dict[str, object]:
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
        'selected_pick_count': sum(len(snapshot.selected_rows) for snapshot in snapshots),
        'complete_return_counts_selected_top10': {str(window): sum(1 for snapshot in snapshots for row in snapshot.selected_rows if row.get(f'{window}d_complete')) for window in windows},
        'missing_forward_exit_count': missing_exits,
    }


def _risk_summary(level_rows: tuple[dict[str, object], ...], tag_rows: tuple[dict[str, object], ...], *, windows: tuple[int, ...]) -> dict[str, object]:
    by_level = {(row['scope'], row['risk_level'], str(row['forward_window_trading_days'])): row for row in level_rows}
    high_vs_non_high = {}
    for window in windows:
        high = by_level.get((GROUP_SELECTED_TOP10, 'HIGH', str(window)))
        low = by_level.get((GROUP_SELECTED_TOP10, 'LOW', str(window)))
        medium = by_level.get((GROUP_SELECTED_TOP10, 'MEDIUM', str(window)))
        high_value = _as_optional_float(None if high is None else high.get('mean_net_excess_return'))
        non_high_values = [
            value for value in (
                _as_optional_float(None if low is None else low.get('mean_net_excess_return')),
                _as_optional_float(None if medium is None else medium.get('mean_net_excess_return')),
            )
            if value is not None
        ]
        high_vs_non_high[str(window)] = None if high_value is None or not non_high_values else high_value - (sum(non_high_values) / len(non_high_values))
    harmful_tags = tuple(
        row['risk_tag']
        for row in tag_rows
        if row['scope'] == GROUP_SELECTED_TOP10
        and str(row['forward_window_trading_days']) == '60'
        and row['risk_tag'] != TAG_NONE
        and _as_float(row.get('complete_count')) >= 5
        and _as_optional_float(row.get('mean_net_excess_return')) is not None
        and _as_float(row['mean_net_excess_return']) < 0.0
        and _as_optional_float(row.get('hit_rate_vs_benchmark')) is not None
        and _as_float(row['hit_rate_vs_benchmark']) < 0.5
    )
    return {
        'high_vs_non_high_selected_top10': high_vs_non_high,
        'harmful_tags_60d': list(dict.fromkeys(harmful_tags)),
        'level_summary_selected_top10': [row for row in level_rows if row['scope'] == GROUP_SELECTED_TOP10],
    }


def _decision_rows(*, decision: str, reasons: tuple[str, ...], risk_summary: dict[str, object]) -> tuple[dict[str, object], ...]:
    return (
        {
            'decision_recommendation': decision,
            'decision_reasons': '; '.join(reasons),
            'high_minus_low_medium_20d_net_excess': risk_summary['high_vs_non_high_selected_top10'].get('20'),
            'high_minus_low_medium_60d_net_excess': risk_summary['high_vs_non_high_selected_top10'].get('60'),
            'harmful_tags_60d': ';'.join(risk_summary['harmful_tags_60d']),
        },
    )


def _split_tags(value: object) -> tuple[str, ...]:
    text = '' if value is None else str(value)
    if not text:
        return (TAG_NONE,)
    return tuple(tag for tag in text.split('|') if tag)


def _render_markdown(result: RiskTagValidationResult) -> str:
    lines = [
        '# Risk Tag Validation',
        '',
        f'- Decision recommendation: `{result.decision_recommendation}`',
        f'- Universe: `{result.universe_id}`',
        f'- Benchmark: `{result.benchmark_ticker}`',
        f'- Incumbent: `{result.incumbent_id}`',
        f'- Valid snapshots: {result.technical_validity["valid_snapshot_count"]}/{result.technical_validity["rebalance_date_count"]}',
        f'- Ranked count total/range: {result.technical_validity["ranked_count_total"]} / {result.technical_validity["ranked_count_min"]}-{result.technical_validity["ranked_count_max"]}',
        '',
        '## High Versus Low/Medium',
    ]
    for window, spread in result.risk_summary['high_vs_non_high_selected_top10'].items():
        lines.append(f'- {window}d net excess spread: `{spread}`')
    lines.extend(['', '## Harmful Tags 60d', f'`{result.risk_summary["harmful_tags_60d"]}`', '', '## Decision Reasons'])
    lines.extend(f'- `{reason}`' for reason in result.decision_reasons)
    lines.extend(['', '## Guardrails', '- Risk tags are diagnostic only; no selection, ranking, UI, ML, or Holdings change.'])
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

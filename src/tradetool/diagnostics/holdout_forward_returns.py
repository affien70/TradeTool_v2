from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median

from tradetool.data import PriceHistoryV2Record, load_price_history_v2_for_tickers
from tradetool.diagnostics.holdout_snapshot import HoldoutSnapshotResult, build_holdout_snapshot

DECISION_CONTINUE = 'continue_to_monthly_holdout_backtest'
DECISION_DATA_GAPS = 'investigate_forward_return_data_gaps'
DECISION_PERFORMANCE = 'investigate_snapshot_candidate_performance'
DECISION_ALIGNMENT = 'fix_forward_return_alignment'
DECISION_BLOCKED_HISTORY = 'blocked_insufficient_forward_history'
EXPECTED_REPORT_FILES = (
    'forward_return_by_candidate_type.csv',
    'forward_return_by_signal.csv',
    'forward_return_decision.csv',
    'forward_return_topn_summary.csv',
    'holdout_forward_returns_summary.json',
    'holdout_forward_returns_summary.md',
    'missing_forward_exits.csv',
    'snapshot_with_forward_returns.csv',
)


@dataclass(frozen=True, slots=True)
class ForwardReturnResult:
    universe_id: str
    universe_source: str
    benchmark_ticker: str
    as_of_date: str
    effective_feature_date: str | None
    data_source: str
    close_input_source: str
    forward_windows: tuple[int, ...]
    snapshot: HoldoutSnapshotResult
    candidate_rows: tuple[dict[str, object], ...]
    signal_summary_rows: tuple[dict[str, object], ...]
    candidate_type_summary_rows: tuple[dict[str, object], ...]
    topn_summary_rows: tuple[dict[str, object], ...]
    missing_exit_rows: tuple[dict[str, object], ...]
    decision_recommendation: str
    decision_reasons: tuple[str, ...]
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'universe_id': self.universe_id,
            'universe_source': self.universe_source,
            'benchmark_ticker': self.benchmark_ticker,
            'as_of_date': self.as_of_date,
            'effective_feature_date': self.effective_feature_date,
            'data_source': self.data_source,
            'close_input_source': self.close_input_source,
            'forward_windows': list(self.forward_windows),
            'snapshot': {
                'snapshot_valid': self.snapshot.snapshot_valid,
                'invalid_reasons': list(self.snapshot.invalid_reasons),
                'requested_stock_ticker_count': self.snapshot.requested_stock_ticker_count,
                'present_ticker_count': self.snapshot.present_ticker_count,
                'feature_complete_count': self.snapshot.feature_complete_count,
                'feature_incomplete_count': self.snapshot.feature_incomplete_count,
                'ranked_count': self.snapshot.ranked_count,
                'signal_counts': dict(self.snapshot.signal_counts),
                'candidate_type_counts': dict(self.snapshot.candidate_type_counts),
                'benchmark_present': self.snapshot.benchmark_present,
                'benchmark_latest_date': self.snapshot.benchmark_latest_date,
            },
            'complete_return_counts': {
                str(window): sum(1 for row in self.candidate_rows if row.get(f'{window}d_complete'))
                for window in self.forward_windows
            },
            'missing_forward_exit_count': len(self.missing_exit_rows),
            'decision_recommendation': self.decision_recommendation,
            'decision_reasons': list(self.decision_reasons),
            'leakage_controls': {
                'snapshot_features_capped_at_as_of_date': True,
                'forward_returns_use_rows_after_as_of_date': True,
                'forward_returns_used_to_change_rank_signal_type': False,
                'ml_score_calculated': False,
                'holdings_adjustment_applied': False,
                'manual_focus_boost_applied': False,
            },
            'generated_at_utc': self.generated_at_utc,
        }


def build_holdout_forward_returns(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str,
    as_of_date: date,
    data_source: str,
    windows: tuple[int, ...] = (20, 60),
    universe_db_path: str | Path | None = None,
) -> ForwardReturnResult:
    snapshot_kwargs = {
        'db_path': db_path,
        'universe_id': universe_id,
        'benchmark_ticker': benchmark_ticker,
        'as_of_date': as_of_date,
        'data_source': data_source,
    }
    if universe_db_path is not None:
        snapshot_kwargs['universe_db_path'] = universe_db_path
    snapshot = build_holdout_snapshot(**snapshot_kwargs)
    tickers = [str(row['ticker']) for row in snapshot.ranked_rows]
    benchmark = benchmark_ticker.strip().upper()
    loaded = load_price_history_v2_for_tickers(
        db_path=str(db_path),
        tickers=[*tickers, benchmark],
        data_source=data_source,
    )
    benchmark_rows = loaded.rows_by_ticker.get(benchmark, ())
    candidate_rows = tuple(
        _candidate_forward_row(
            snapshot_row=row,
            ticker_rows=loaded.rows_by_ticker.get(str(row['ticker']), ()),
            benchmark_rows=benchmark_rows,
            as_of_date=as_of_date,
            windows=windows,
        )
        for row in snapshot.ranked_rows
    )
    signal_summary = _aggregate_by_group(candidate_rows, group_field='trade_signal', windows=windows)
    candidate_type_summary = _aggregate_by_group(candidate_rows, group_field='candidate_type', windows=windows)
    topn_summary = _aggregate_topn(candidate_rows, windows=windows)
    missing_rows = _missing_exit_rows(candidate_rows, windows=windows)
    decision, reasons = recommend_next_action(
        snapshot=snapshot,
        candidate_rows=candidate_rows,
        windows=windows,
        topn_summary_rows=topn_summary,
    )
    return ForwardReturnResult(
        universe_id=universe_id,
        universe_source=snapshot.universe_source,
        benchmark_ticker=benchmark,
        as_of_date=as_of_date.isoformat(),
        effective_feature_date=snapshot.effective_feature_date,
        data_source=data_source,
        close_input_source='adjusted_close',
        forward_windows=windows,
        snapshot=snapshot,
        candidate_rows=candidate_rows,
        signal_summary_rows=signal_summary,
        candidate_type_summary_rows=candidate_type_summary,
        topn_summary_rows=topn_summary,
        missing_exit_rows=missing_rows,
        decision_recommendation=decision,
        decision_reasons=reasons,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_holdout_forward_return_outputs(*, result: ForwardReturnResult, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'holdout_forward_returns_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'holdout_forward_returns_summary.md').write_text(_render_markdown(result), encoding='utf-8')
    _write_csv(path / 'snapshot_with_forward_returns.csv', result.candidate_rows)
    _write_csv(path / 'forward_return_by_signal.csv', result.signal_summary_rows)
    _write_csv(path / 'forward_return_by_candidate_type.csv', result.candidate_type_summary_rows)
    _write_csv(path / 'forward_return_topn_summary.csv', result.topn_summary_rows)
    _write_csv(path / 'missing_forward_exits.csv', result.missing_exit_rows)
    _write_csv(
        path / 'forward_return_decision.csv',
        [{'decision_recommendation': result.decision_recommendation, 'decision_reasons': '; '.join(result.decision_reasons)}],
    )


def recommend_next_action(
    *,
    snapshot: HoldoutSnapshotResult,
    candidate_rows: tuple[dict[str, object], ...],
    windows: tuple[int, ...],
    topn_summary_rows: tuple[dict[str, object], ...],
) -> tuple[str, tuple[str, ...]]:
    if not snapshot.snapshot_valid or not snapshot.benchmark_present:
        return DECISION_BLOCKED_HISTORY, ('snapshot_or_benchmark_invalid_for_forward_returns',)
    if not candidate_rows:
        return DECISION_BLOCKED_HISTORY, ('no_ranked_candidates_for_forward_returns',)
    complete_counts = {window: sum(1 for row in candidate_rows if row.get(f'{window}d_complete')) for window in windows}
    if any(count == 0 for count in complete_counts.values()):
        return DECISION_BLOCKED_HISTORY, ('not_enough_future_history_for_requested_windows',)
    minimum = max(1, len(candidate_rows) // 2)
    if any(count < minimum for count in complete_counts.values()):
        return DECISION_DATA_GAPS, ('many_candidates_lack_forward_exit_prices',)
    if any(_has_suspicious_alignment(row, windows) for row in candidate_rows):
        return DECISION_ALIGNMENT, ('benchmark_alignment_dates_are_suspicious',)
    top10_60 = next(
        (
            row for row in topn_summary_rows
            if row.get('group') == 'raw_top_10' and row.get('forward_window_trading_days') == 60
        ),
        None,
    )
    top10_20 = next(
        (
            row for row in topn_summary_rows
            if row.get('group') == 'raw_top_10' and row.get('forward_window_trading_days') == 20
        ),
        None,
    )
    if (
        top10_20 is not None and
        top10_60 is not None and
        _as_float(top10_20.get('mean_excess_return')) <= -0.10 and
        _as_float(top10_60.get('mean_excess_return')) <= -0.10 and
        _as_float(top10_20.get('hit_rate_vs_benchmark')) <= 0.20 and
        _as_float(top10_60.get('hit_rate_vs_benchmark')) <= 0.20
    ):
        return DECISION_PERFORMANCE, ('top_candidates_underperformed_materially_in_single_snapshot',)
    return DECISION_CONTINUE, ('forward_returns_available_and_alignment_controls_passed',)


def _candidate_forward_row(
    *,
    snapshot_row: dict[str, object],
    ticker_rows: tuple[PriceHistoryV2Record, ...],
    benchmark_rows: tuple[PriceHistoryV2Record, ...],
    as_of_date: date,
    windows: tuple[int, ...],
) -> dict[str, object]:
    output = {
        'ticker': snapshot_row['ticker'],
        'raw_rank': snapshot_row['raw_rank'],
        'raw_score': snapshot_row['raw_score'],
        'trade_signal': snapshot_row['trade_signal'],
        'candidate_type': snapshot_row['candidate_type'],
        'latest_feature_date': snapshot_row.get('feature_date'),
    }
    future_rows = tuple(row for row in ticker_rows if row.price_date > as_of_date)
    entry = future_rows[0] if future_rows else None
    output['entry_date'] = None if entry is None else entry.price_date.isoformat()
    output['entry_close'] = None if entry is None else entry.adjusted_close
    missing_reasons: list[str] = []
    if entry is None:
        missing_reasons.append('missing_ticker_entry_after_as_of_date')

    for window in windows:
        values = _calculate_window_return(
            entry=entry,
            future_rows=future_rows,
            benchmark_rows=benchmark_rows,
            window=window,
        )
        output.update(values)
        reason = values.get(f'{window}d_missing_reason')
        if reason:
            missing_reasons.append(str(reason))
    output['missing_reason'] = '; '.join(dict.fromkeys(missing_reasons))
    return output


def _calculate_window_return(
    *,
    entry: PriceHistoryV2Record | None,
    future_rows: tuple[PriceHistoryV2Record, ...],
    benchmark_rows: tuple[PriceHistoryV2Record, ...],
    window: int,
) -> dict[str, object]:
    prefix = f'{window}d'
    if entry is None:
        return _empty_window(prefix, 'missing_ticker_entry_after_as_of_date')
    if len(future_rows) <= window:
        return _empty_window(prefix, 'missing_ticker_exit')
    exit_row = future_rows[window]
    benchmark_entry = _first_row_on_or_after(benchmark_rows, entry.price_date)
    if benchmark_entry is None:
        return _empty_window(prefix, 'missing_benchmark_entry', entry=entry, exit_row=exit_row)
    benchmark_exit = _first_row_on_or_after(benchmark_rows, exit_row.price_date)
    if benchmark_exit is None:
        return _empty_window(prefix, 'missing_benchmark_exit', entry=entry, exit_row=exit_row, benchmark_entry=benchmark_entry)
    ticker_return = (exit_row.adjusted_close / entry.adjusted_close) - 1.0
    benchmark_return = (benchmark_exit.adjusted_close / benchmark_entry.adjusted_close) - 1.0
    return {
        f'{prefix}_complete': True,
        f'{prefix}_exit_date': exit_row.price_date.isoformat(),
        f'{prefix}_return': ticker_return,
        f'{prefix}_benchmark_entry_date': benchmark_entry.price_date.isoformat(),
        f'{prefix}_benchmark_exit_date': benchmark_exit.price_date.isoformat(),
        f'{prefix}_benchmark_return': benchmark_return,
        f'{prefix}_excess_return': ticker_return - benchmark_return,
        f'{prefix}_missing_reason': '',
    }


def _empty_window(
    prefix: str,
    reason: str,
    *,
    entry: PriceHistoryV2Record | None = None,
    exit_row: PriceHistoryV2Record | None = None,
    benchmark_entry: PriceHistoryV2Record | None = None,
) -> dict[str, object]:
    return {
        f'{prefix}_complete': False,
        f'{prefix}_exit_date': None if exit_row is None else exit_row.price_date.isoformat(),
        f'{prefix}_return': None,
        f'{prefix}_benchmark_entry_date': None if benchmark_entry is None else benchmark_entry.price_date.isoformat(),
        f'{prefix}_benchmark_exit_date': None,
        f'{prefix}_benchmark_return': None,
        f'{prefix}_excess_return': None,
        f'{prefix}_missing_reason': reason if entry is None or exit_row is None or benchmark_entry is None else reason,
    }


def _first_row_on_or_after(rows: tuple[PriceHistoryV2Record, ...], target: date) -> PriceHistoryV2Record | None:
    return next((row for row in rows if row.price_date >= target), None)


def _aggregate_by_group(
    rows: tuple[dict[str, object], ...],
    *,
    group_field: str,
    windows: tuple[int, ...],
) -> tuple[dict[str, object], ...]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[group_field])].append(row)
    return tuple(
        _aggregate_rows(group=name, rows=tuple(group_rows), window=window)
        for name, group_rows in sorted(grouped.items())
        for window in windows
    )


def _aggregate_topn(rows: tuple[dict[str, object], ...], *, windows: tuple[int, ...]) -> tuple[dict[str, object], ...]:
    specs = (('raw_top_5', 5), ('raw_top_10', 10), ('raw_top_20', 20))
    return tuple(
        _aggregate_rows(group=name, rows=tuple(row for row in rows if int(row['raw_rank']) <= limit), window=window)
        for name, limit in specs
        for window in windows
    )


def _aggregate_rows(*, group: str, rows: tuple[dict[str, object], ...], window: int) -> dict[str, object]:
    complete = tuple(row for row in rows if row.get(f'{window}d_complete'))
    returns = [_as_float(row.get(f'{window}d_return')) for row in complete]
    benchmarks = [_as_float(row.get(f'{window}d_benchmark_return')) for row in complete]
    excess = [_as_float(row.get(f'{window}d_excess_return')) for row in complete]
    return {
        'group': group,
        'forward_window_trading_days': window,
        'count': len(rows),
        'complete_count': len(complete),
        'mean_return': _mean(returns),
        'median_return': None if not returns else median(returns),
        'mean_benchmark_return': _mean(benchmarks),
        'mean_excess_return': _mean(excess),
        'hit_rate_vs_benchmark': _mean([1.0 if value > 0 else 0.0 for value in excess]),
        'positive_return_rate': _mean([1.0 if value > 0 else 0.0 for value in returns]),
    }


def _missing_exit_rows(rows: tuple[dict[str, object], ...], *, windows: tuple[int, ...]) -> tuple[dict[str, object], ...]:
    missing: list[dict[str, object]] = []
    for row in rows:
        for window in windows:
            reason = row.get(f'{window}d_missing_reason')
            if reason:
                missing.append(
                    {
                        'ticker': row['ticker'],
                        'raw_rank': row['raw_rank'],
                        'trade_signal': row['trade_signal'],
                        'candidate_type': row['candidate_type'],
                        'forward_window_trading_days': window,
                        'missing_reason': reason,
                    }
                )
    return tuple(missing)


def _has_suspicious_alignment(row: dict[str, object], windows: tuple[int, ...]) -> bool:
    for window in windows:
        if not row.get(f'{window}d_complete'):
            continue
        exit_date = date.fromisoformat(str(row[f'{window}d_exit_date']))
        benchmark_exit_date = date.fromisoformat(str(row[f'{window}d_benchmark_exit_date']))
        if benchmark_exit_date < exit_date or (benchmark_exit_date - exit_date).days > 7:
            return True
        entry_date = date.fromisoformat(str(row['entry_date']))
        benchmark_entry_date = date.fromisoformat(str(row[f'{window}d_benchmark_entry_date']))
        if benchmark_entry_date < entry_date or (benchmark_entry_date - entry_date).days > 7:
            return True
    return False


def _render_markdown(result: ForwardReturnResult) -> str:
    summary = result.to_summary_dict()
    lines = [
        '# Holdout Forward Returns Summary',
        '',
        '## Technical Summary',
        f"- Decision recommendation: `{result.decision_recommendation}`",
        f"- Universe source: `{result.universe_source}`",
        f"- Requested ticker count: {result.snapshot.requested_stock_ticker_count}",
        f"- Present ticker count: {result.snapshot.present_ticker_count}",
        f"- As-of date: `{result.as_of_date}`",
        f"- Effective feature date: `{result.effective_feature_date or 'none'}`",
        f"- Ranked count: {result.snapshot.ranked_count}",
        f"- Benchmark: `{result.benchmark_ticker}`",
        f"- Benchmark present: `{result.snapshot.benchmark_present}`",
        f"- Close input source: `{result.close_input_source}`",
        f"- Forward windows: `{list(result.forward_windows)}`",
        f"- Complete return counts: `{summary['complete_return_counts']}`",
        '',
        '## Leakage Controls',
        '- Snapshot features used no rows after the as-of date.',
        '- Forward returns used only rows strictly after the as-of date.',
        '- Forward returns did not change rank, signal, or candidate type.',
        '- No ML score, Holdings adjustment, or manual focus boost was applied.',
        '',
        '## Top-N Summary',
    ]
    for row in result.topn_summary_rows:
        lines.append(
            f"- {row['group']} {row['forward_window_trading_days']}d: "
            f"complete={row['complete_count']}/{row['count']} "
            f"mean_return={row['mean_return']} mean_excess={row['mean_excess_return']}"
        )
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

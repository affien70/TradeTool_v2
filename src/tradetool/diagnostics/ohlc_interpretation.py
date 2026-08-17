from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradetool.diagnostics.invalid_ohlc import InvalidOhlcDiagnosticsResult, InvalidOhlcRow, build_invalid_ohlc_diagnostics

_LATEST_WINDOW_ROWS = 252


@dataclass(frozen=True, slots=True)
class OhlcViolationPatternRow:
    pattern: str
    invalid_row_count: int
    affected_ticker_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            'pattern': self.pattern,
            'invalid_row_count': self.invalid_row_count,
            'affected_ticker_count': self.affected_ticker_count,
        }


@dataclass(frozen=True, slots=True)
class OhlcRatioSampleRow:
    ticker: str
    price_date: str | None
    violation_pattern: str
    likely_interpretation: str
    close_over_low: float | None
    close_over_high: float | None
    close_over_open: float | None
    low_over_close: float | None
    high_over_close: float | None
    within_latest_252_rows: bool | None

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'price_date': self.price_date,
            'violation_pattern': self.violation_pattern,
            'likely_interpretation': self.likely_interpretation,
            'close_over_low': self.close_over_low,
            'close_over_high': self.close_over_high,
            'close_over_open': self.close_over_open,
            'low_over_close': self.low_over_close,
            'high_over_close': self.high_over_close,
            'within_latest_252_rows': self.within_latest_252_rows,
        }


@dataclass(frozen=True, slots=True)
class OhlcByTickerInterpretationRow:
    ticker: str
    invalid_row_count: int
    dominant_pattern: str
    dominant_interpretation: str
    first_invalid_date: str | None
    latest_invalid_date: str | None
    invalid_rows_within_latest_252: int
    invalid_rows_recent: int

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'invalid_row_count': self.invalid_row_count,
            'dominant_pattern': self.dominant_pattern,
            'dominant_interpretation': self.dominant_interpretation,
            'first_invalid_date': self.first_invalid_date,
            'latest_invalid_date': self.latest_invalid_date,
            'invalid_rows_within_latest_252': self.invalid_rows_within_latest_252,
            'invalid_rows_recent': self.invalid_rows_recent,
        }


@dataclass(frozen=True, slots=True)
class OhlcByDateInterpretationRow:
    price_date: str
    invalid_row_count: int
    affected_ticker_count: int
    dominant_pattern: str

    def to_dict(self) -> dict[str, object]:
        return {
            'price_date': self.price_date,
            'invalid_row_count': self.invalid_row_count,
            'affected_ticker_count': self.affected_ticker_count,
            'dominant_pattern': self.dominant_pattern,
        }


@dataclass(frozen=True, slots=True)
class OhlcInterpretationRow:
    invalid_row: InvalidOhlcRow
    violation_pattern: str
    likely_interpretation: str
    close_over_low: float | None
    close_over_high: float | None
    close_over_open: float | None
    low_over_close: float | None
    high_over_close: float | None


@dataclass(frozen=True, slots=True)
class OhlcInterpretationAuditResult:
    invalid_ohlc: InvalidOhlcDiagnosticsResult
    recommendation: str
    dominant_violation_pattern: str
    likely_source_interpretation: str
    ratio_statistics: Mapping[str, Mapping[str, float | None]]
    month_distribution: Mapping[str, int]
    latest_252_invalid_row_count: int
    latest_252_affected_ticker_count: int
    few_date_cluster_share: float
    broad_date_distribution: bool
    refresh_design_implications: Mapping[str, str]
    generated_at_utc: str
    interpreted_rows: tuple[OhlcInterpretationRow, ...]
    violation_pattern_rows: tuple[OhlcViolationPatternRow, ...]
    ratio_sample_rows: tuple[OhlcRatioSampleRow, ...]
    by_ticker_rows: tuple[OhlcByTickerInterpretationRow, ...]
    by_date_rows: tuple[OhlcByDateInterpretationRow, ...]

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'universe_id': self.invalid_ohlc.universe_id,
            'universe_source': self.invalid_ohlc.universe_source,
            'detected_price_table': self.invalid_ohlc.detected_price_table,
            'price_date_column': self.invalid_ohlc.price_date_column,
            'invalid_row_count': self.invalid_ohlc.invalid_row_count,
            'affected_ticker_count': self.invalid_ohlc.affected_ticker_count,
            'dominant_violation_pattern': self.dominant_violation_pattern,
            'likely_source_interpretation': self.likely_source_interpretation,
            'recommendation': self.recommendation,
            'rows_with_recent_invalid_dates': self.invalid_ohlc.rows_with_recent_invalid_dates,
            'rows_with_historical_invalid_dates': self.invalid_ohlc.rows_with_historical_invalid_dates,
            'latest_252_invalid_row_count': self.latest_252_invalid_row_count,
            'latest_252_affected_ticker_count': self.latest_252_affected_ticker_count,
            'few_date_cluster_share': self.few_date_cluster_share,
            'broad_date_distribution': self.broad_date_distribution,
            'ratio_statistics': {
                key: dict(value)
                for key, value in self.ratio_statistics.items()
            },
            'month_distribution': dict(self.month_distribution),
            'refresh_design_implications': dict(self.refresh_design_implications),
            'generated_at_utc': self.generated_at_utc,
        }


def build_ohlc_interpretation_audit(
    *,
    db_path: str | Path,
    universe_id: str,
    price_table: str | None = None,
) -> OhlcInterpretationAuditResult:
    invalid_ohlc = build_invalid_ohlc_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        price_table=price_table,
    )
    interpreted_rows = tuple(_interpret_row(row) for row in invalid_ohlc.rows)
    pattern_counter: Counter[str] = Counter()
    pattern_tickers: defaultdict[str, set[str]] = defaultdict(set)
    interpretation_counter: Counter[str] = Counter()
    ratio_values: dict[str, list[float]] = defaultdict(list)
    month_counter: Counter[str] = Counter()
    by_date_counter: Counter[str] = Counter()
    by_date_tickers: defaultdict[str, set[str]] = defaultdict(set)
    by_date_patterns: defaultdict[str, Counter[str]] = defaultdict(Counter)
    by_ticker_rows_map: defaultdict[str, list[OhlcInterpretationRow]] = defaultdict(list)
    latest_252_tickers: set[str] = set()

    for row in interpreted_rows:
        pattern_counter[row.violation_pattern] += 1
        pattern_tickers[row.violation_pattern].add(row.invalid_row.ticker)
        interpretation_counter[row.likely_interpretation] += 1
        by_ticker_rows_map[row.invalid_row.ticker].append(row)
        if row.invalid_row.price_date:
            month_counter[row.invalid_row.price_date[:7]] += 1
            by_date_counter[row.invalid_row.price_date] += 1
            by_date_tickers[row.invalid_row.price_date].add(row.invalid_row.ticker)
            by_date_patterns[row.invalid_row.price_date][row.violation_pattern] += 1
        if row.invalid_row.within_latest_252_rows is True:
            latest_252_tickers.add(row.invalid_row.ticker)
        for label, value in (
            ('close_over_low', row.close_over_low),
            ('close_over_high', row.close_over_high),
            ('close_over_open', row.close_over_open),
            ('low_over_close', row.low_over_close),
            ('high_over_close', row.high_over_close),
        ):
            if value is not None:
                ratio_values[label].append(value)

    violation_pattern_rows = tuple(
        OhlcViolationPatternRow(pattern=pattern, invalid_row_count=count, affected_ticker_count=len(pattern_tickers[pattern]))
        for pattern, count in sorted(pattern_counter.items(), key=lambda item: (-item[1], item[0]))
    )
    ratio_sample_rows = tuple(
        OhlcRatioSampleRow(
            ticker=row.invalid_row.ticker,
            price_date=row.invalid_row.price_date,
            violation_pattern=row.violation_pattern,
            likely_interpretation=row.likely_interpretation,
            close_over_low=row.close_over_low,
            close_over_high=row.close_over_high,
            close_over_open=row.close_over_open,
            low_over_close=row.low_over_close,
            high_over_close=row.high_over_close,
            within_latest_252_rows=row.invalid_row.within_latest_252_rows,
        )
        for row in interpreted_rows
    )
    by_ticker_rows = tuple(
        sorted(
            (
                OhlcByTickerInterpretationRow(
                    ticker=ticker,
                    invalid_row_count=len(rows),
                    dominant_pattern=_dominant_label(Counter(row.violation_pattern for row in rows)),
                    dominant_interpretation=_dominant_label(Counter(row.likely_interpretation for row in rows)),
                    first_invalid_date=min((row.invalid_row.price_date for row in rows if row.invalid_row.price_date), default=None),
                    latest_invalid_date=max((row.invalid_row.price_date for row in rows if row.invalid_row.price_date), default=None),
                    invalid_rows_within_latest_252=sum(1 for row in rows if row.invalid_row.within_latest_252_rows is True),
                    invalid_rows_recent=sum(1 for row in rows if row.invalid_row.recency_bucket in {'on_universe_max_date', 'within_30_days_of_universe_max'}),
                )
                for ticker, rows in by_ticker_rows_map.items()
            ),
            key=lambda row: (-row.invalid_row_count, row.ticker),
        )
    )
    by_date_rows = tuple(
        OhlcByDateInterpretationRow(
            price_date=price_date,
            invalid_row_count=count,
            affected_ticker_count=len(by_date_tickers[price_date]),
            dominant_pattern=_dominant_label(by_date_patterns[price_date]),
        )
        for price_date, count in sorted(by_date_counter.items(), key=lambda item: (-item[1], item[0]))
    )

    dominant_pattern = violation_pattern_rows[0].pattern if violation_pattern_rows else 'other'
    close_outside_only_count = sum(
        1
        for row in interpreted_rows
        if row.likely_interpretation == 'likely_adjusted_close_vs_unadjusted_ohl'
    )
    bad_raw_data_count = sum(
        1
        for row in interpreted_rows
        if row.violation_pattern in {'high_low_inverted', 'missing_or_nonpositive_price', 'open_outside_high_low'}
        and row.likely_interpretation != 'likely_adjusted_close_vs_unadjusted_ohl'
    )
    if interpreted_rows and close_outside_only_count / len(interpreted_rows) >= 0.7:
        recommendation = 'likely_source_adjustment_mismatch'
    elif bad_raw_data_count / max(len(interpreted_rows), 1) >= 0.3:
        recommendation = 'likely_bad_raw_data'
    else:
        recommendation = 'needs_manual_inspection'

    few_date_total = sum(row.invalid_row_count for row in by_date_rows[:5])
    few_date_cluster_share = 0.0 if not interpreted_rows else few_date_total / len(interpreted_rows)
    broad_date_distribution = few_date_cluster_share < 0.5
    ratio_statistics = {
        label: _summarize_ratios(values)
        for label, values in sorted(ratio_values.items())
    }
    likely_source_interpretation = recommendation
    refresh_design_implications = _build_refresh_design_implications(recommendation)

    return OhlcInterpretationAuditResult(
        invalid_ohlc=invalid_ohlc,
        recommendation=recommendation,
        dominant_violation_pattern=dominant_pattern,
        likely_source_interpretation=likely_source_interpretation,
        ratio_statistics=ratio_statistics,
        month_distribution=dict(sorted(month_counter.items())),
        latest_252_invalid_row_count=invalid_ohlc.rows_within_latest_252_window,
        latest_252_affected_ticker_count=len(latest_252_tickers),
        few_date_cluster_share=few_date_cluster_share,
        broad_date_distribution=broad_date_distribution,
        refresh_design_implications=refresh_design_implications,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        interpreted_rows=interpreted_rows,
        violation_pattern_rows=violation_pattern_rows,
        ratio_sample_rows=ratio_sample_rows,
        by_ticker_rows=by_ticker_rows,
        by_date_rows=by_date_rows,
    )


def write_ohlc_interpretation_outputs(*, result: OhlcInterpretationAuditResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / 'ohlc_violation_patterns.csv', [row.to_dict() for row in result.violation_pattern_rows])
    _write_csv(out_dir / 'ohlc_ratio_samples.csv', [row.to_dict() for row in result.ratio_sample_rows])
    _write_csv(out_dir / 'ohlc_by_ticker_interpretation.csv', [row.to_dict() for row in result.by_ticker_rows])
    _write_csv(out_dir / 'ohlc_by_date_interpretation.csv', [row.to_dict() for row in result.by_date_rows])
    (out_dir / 'ohlc_interpretation_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'ohlc_interpretation_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')


def _interpret_row(row: InvalidOhlcRow) -> OhlcInterpretationRow:
    open_value = row.open_value
    high_value = row.high_value
    low_value = row.low_value
    close_value = row.close_value
    has_missing_or_nonpositive = any(
        value is None or value <= 0
        for value in (open_value, high_value, low_value, close_value)
    )
    high_low_inverted = high_value is not None and low_value is not None and high_value < low_value
    close_below_low = close_value is not None and low_value is not None and close_value < low_value
    close_above_high = close_value is not None and high_value is not None and close_value > high_value
    open_outside = (
        open_value is not None
        and low_value is not None
        and high_value is not None
        and (open_value < low_value or open_value > high_value)
    )
    if has_missing_or_nonpositive:
        pattern = 'missing_or_nonpositive_price'
    elif high_low_inverted:
        pattern = 'high_low_inverted'
    elif open_outside:
        pattern = 'open_outside_high_low'
    elif close_below_low:
        pattern = 'close_below_low'
    elif close_above_high:
        pattern = 'close_above_high'
    else:
        pattern = 'other'
    if (
        pattern in {'close_below_low', 'close_above_high'}
        and not high_low_inverted
        and not open_outside
        and open_value is not None
        and high_value is not None
        and low_value is not None
        and close_value is not None
        and low_value <= open_value <= high_value
    ):
        likely_interpretation = 'likely_adjusted_close_vs_unadjusted_ohl'
    elif pattern == 'missing_or_nonpositive_price':
        likely_interpretation = 'likely_bad_raw_data'
    elif pattern in {'high_low_inverted', 'open_outside_high_low'}:
        likely_interpretation = 'likely_bad_raw_data'
    else:
        likely_interpretation = 'needs_manual_inspection'
    return OhlcInterpretationRow(
        invalid_row=row,
        violation_pattern=pattern,
        likely_interpretation=likely_interpretation,
        close_over_low=_safe_ratio(close_value, low_value),
        close_over_high=_safe_ratio(close_value, high_value),
        close_over_open=_safe_ratio(close_value, open_value),
        low_over_close=_safe_ratio(low_value, close_value),
        high_over_close=_safe_ratio(high_value, close_value),
    )


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _summarize_ratios(values: Sequence[float]) -> Mapping[str, float | None]:
    if not values:
        return {'min': None, 'p10': None, 'p25': None, 'median': None, 'p75': None, 'p90': None, 'max': None}
    sorted_values = sorted(values)
    return {
        'min': sorted_values[0],
        'p10': _percentile(sorted_values, 0.10),
        'p25': _percentile(sorted_values, 0.25),
        'median': _percentile(sorted_values, 0.50),
        'p75': _percentile(sorted_values, 0.75),
        'p90': _percentile(sorted_values, 0.90),
        'max': sorted_values[-1],
    }


def _percentile(sorted_values: Sequence[float], percentile: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = percentile * (len(sorted_values) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = position - lower_index
    return sorted_values[lower_index] + (sorted_values[upper_index] - sorted_values[lower_index]) * fraction


def _dominant_label(counter: Counter[str]) -> str:
    if not counter:
        return 'other'
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _build_refresh_design_implications(recommendation: str) -> Mapping[str, str]:
    copied_db_repair_needed = 'yes' if recommendation != 'likely_bad_raw_data' else 'yes'
    safe_for_refresh_testing = 'no'
    return {
        'store_raw_close_and_adjusted_close_separately': 'yes',
        'use_adjusted_close_consistently_for_features': 'yes',
        'validate_raw_ohlc_separately_from_adjusted_close': 'yes',
        'legacy_db_safe_for_refresh_testing_without_correction': safe_for_refresh_testing,
        'copied_db_repair_or_migration_needed_before_live_refresh': copied_db_repair_needed,
    }


def _render_summary_markdown(result: OhlcInterpretationAuditResult) -> str:
    lines = [
        '# OHLC Interpretation Summary',
        '',
        '## Executive conclusion',
        '',
        f'- Recommendation: {result.recommendation}',
        f'- Dominant violation pattern: {result.dominant_violation_pattern}',
        f'- Likely source interpretation: {result.likely_source_interpretation}',
        '',
        '## Scope',
        '',
        f'- Universe id: {result.invalid_ohlc.universe_id}',
        f'- Universe source: {result.invalid_ohlc.universe_source}',
        f'- Price table: {result.invalid_ohlc.detected_price_table}',
        f'- Price date column: {result.invalid_ohlc.price_date_column}',
        f'- Invalid rows: {result.invalid_ohlc.invalid_row_count}',
        f'- Affected tickers: {result.invalid_ohlc.affected_ticker_count}',
        '',
        '## Timing and concentration',
        '',
        f'- Historical invalid rows: {result.invalid_ohlc.rows_with_historical_invalid_dates}',
        f'- Recent invalid rows: {result.invalid_ohlc.rows_with_recent_invalid_dates}',
        f'- Invalid rows in latest {_LATEST_WINDOW_ROWS}-row window: {result.latest_252_invalid_row_count}',
        f'- Affected tickers in latest {_LATEST_WINDOW_ROWS}-row window: {result.latest_252_affected_ticker_count}',
        f'- Top 5 date cluster share: {result.few_date_cluster_share:.3f}',
        f'- Broad date distribution: {result.broad_date_distribution}',
        '',
        '## Refresh design implications',
        '',
    ]
    for key, value in result.refresh_design_implications.items():
        lines.append(f'- {key}: {value}')
    lines.extend(['', '## Top patterns', ''])
    for row in result.violation_pattern_rows[:10]:
        lines.append(f'- {row.pattern}: {row.invalid_row_count} rows across {row.affected_ticker_count} tickers')
    return '\n'.join(lines) + '\n'


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = ['value']
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

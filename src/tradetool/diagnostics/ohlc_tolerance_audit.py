from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from tradetool.data.market_data_schema import MarketDataRow, _validate_market_data_row
from tradetool.data.market_data_source import (
    MarketDataSource,
    MarketDataSourceDependencyError,
    MarketDataSourceFetchResult,
    SourceMarketDataRow,
    TickerFetchStatus,
    build_market_data_source,
    normalize_source_rows_for_v2,
)


TOLERANCE_SCENARIOS: tuple[tuple[str, float | None, float | None], ...] = (
    ('strict_current', None, None),
    ('absolute_0_01', 0.01, None),
    ('absolute_0_05', 0.05, None),
    ('absolute_0_10', 0.10, None),
    ('absolute_0_25', 0.25, None),
    ('relative_0_001', None, 0.001),
    ('relative_0_0025', None, 0.0025),
    ('relative_0_005', None, 0.005),
    ('combined_abs_0_10_rel_0_0025', 0.10, 0.0025),
    ('combined_abs_0_25_rel_0_005', 0.25, 0.005),
)

TOLERABLE_BOUNDARY_REASONS = {
    'raw_high_lower_than_raw_open',
    'raw_high_lower_than_raw_close',
    'raw_low_higher_than_raw_open',
    'raw_low_higher_than_raw_close',
}


@dataclass(frozen=True, slots=True)
class OHLCToleranceViolation:
    ticker: str
    price_date: str
    reason: str
    raw_open: float | None
    raw_high: float | None
    raw_low: float | None
    raw_close: float | None
    adjusted_close: float
    absolute_violation_amount: float | None
    relative_violation_amount_vs_raw_close: float | None
    adjusted_close_differs_from_raw_close: bool
    source: str

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'price_date': self.price_date,
            'reason': self.reason,
            'raw_open': self.raw_open,
            'raw_high': self.raw_high,
            'raw_low': self.raw_low,
            'raw_close': self.raw_close,
            'adjusted_close': self.adjusted_close,
            'absolute_violation_amount': self.absolute_violation_amount,
            'relative_violation_amount_vs_raw_close': self.relative_violation_amount_vs_raw_close,
            'adjusted_close_differs_from_raw_close': self.adjusted_close_differs_from_raw_close,
            'source': self.source,
        }


@dataclass(frozen=True, slots=True)
class OHLCToleranceTickerSummary:
    ticker: str
    total_rows: int
    strict_invalid_rows: int
    violation_count: int
    max_absolute_violation_amount: float | None
    max_relative_violation_amount_vs_raw_close: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'total_rows': self.total_rows,
            'strict_invalid_rows': self.strict_invalid_rows,
            'violation_count': self.violation_count,
            'max_absolute_violation_amount': self.max_absolute_violation_amount,
            'max_relative_violation_amount_vs_raw_close': self.max_relative_violation_amount_vs_raw_close,
        }


@dataclass(frozen=True, slots=True)
class OHLCToleranceScenario:
    scenario: str
    total_rows: int
    strict_invalid_rows: int
    tolerated_invalid_rows: int
    remaining_invalid_rows: int
    affected_tickers: tuple[str, ...]
    sntia_2026_09_09_would_pass: bool | None

    def to_dict(self) -> dict[str, object]:
        return {
            'scenario': self.scenario,
            'total_rows': self.total_rows,
            'strict_invalid_rows': self.strict_invalid_rows,
            'tolerated_invalid_rows': self.tolerated_invalid_rows,
            'remaining_invalid_rows': self.remaining_invalid_rows,
            'affected_tickers': '; '.join(self.affected_tickers),
            'sntia_2026_09_09_would_pass': self.sntia_2026_09_09_would_pass,
        }


@dataclass(frozen=True, slots=True)
class OHLCToleranceAuditResult:
    source_name: str
    requested_tickers: tuple[str, ...]
    fetched_tickers: tuple[str, ...]
    missing_tickers: tuple[str, ...]
    provider_warning: str | None
    provider_error: str | None
    total_rows: int
    strict_invalid_rows: int
    violation_count: int
    violation_reason_counts: dict[str, int]
    ticker_summaries: tuple[OHLCToleranceTickerSummary, ...]
    violations: tuple[OHLCToleranceViolation, ...]
    scenarios: tuple[OHLCToleranceScenario, ...]
    recommendation: str
    ticker_statuses: tuple[TickerFetchStatus, ...]
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'source_name': self.source_name,
            'requested_tickers': list(self.requested_tickers),
            'fetched_tickers': list(self.fetched_tickers),
            'missing_tickers': list(self.missing_tickers),
            'provider_warning': self.provider_warning,
            'provider_error': self.provider_error,
            'total_rows': self.total_rows,
            'strict_invalid_rows': self.strict_invalid_rows,
            'violation_count': self.violation_count,
            'violation_reason_counts': dict(self.violation_reason_counts),
            'recommendation': self.recommendation,
            'generated_at_utc': self.generated_at_utc,
        }


def build_ohlc_tolerance_audit(
    *,
    tickers: list[str],
    start_date: date,
    end_date: date,
    source_name: str,
    source_override: MarketDataSource | None = None,
) -> OHLCToleranceAuditResult:
    generated_at_utc = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        source = source_override or build_market_data_source(source_name)
        fetch_result = source.fetch_daily_rows(
            tickers=tickers,
            start_date=start_date,
            end_date=end_date,
        )
        provider_error = fetch_result.provider_error
        provider_warning = fetch_result.provider_warning
    except MarketDataSourceDependencyError as exc:
        requested = tuple(ticker.strip().upper() for ticker in tickers if ticker.strip())
        fetch_result = MarketDataSourceFetchResult(
            source_name=source_name,
            requested_tickers=requested,
            rows=(),
            ticker_statuses=tuple(
                TickerFetchStatus(
                    ticker=ticker,
                    fetched=False,
                    row_count=0,
                    status='provider_unavailable',
                    message=str(exc),
                )
                for ticker in requested
            ),
            provider_error=str(exc),
        )
        provider_error = str(exc)
        provider_warning = None

    market_rows = normalize_source_rows_for_v2(
        rows=fetch_result.rows,
        created_at_utc=generated_at_utc,
        updated_at_utc=generated_at_utc,
    )
    validation_by_key = {
        row.key(): _validate_market_data_row(row)
        for row in market_rows
    }
    violations = tuple(
        violation
        for market_row in market_rows
        for violation in _build_violations_for_row(market_row, validation_by_key[market_row.key()])
    )
    scenarios = tuple(
        _evaluate_scenario(
            scenario_name=scenario_name,
            absolute_tolerance=absolute_tolerance,
            relative_tolerance=relative_tolerance,
            market_rows=market_rows,
            validation_by_key=validation_by_key,
        )
        for scenario_name, absolute_tolerance, relative_tolerance in TOLERANCE_SCENARIOS
    )
    fetched_tickers = tuple(sorted({status.ticker for status in fetch_result.ticker_statuses if status.fetched}))
    missing_tickers = tuple(sorted(status.ticker for status in fetch_result.ticker_statuses if not status.fetched))
    violation_reason_counts = dict(sorted(Counter(violation.reason for violation in violations).items()))
    ticker_summaries = _build_ticker_summaries(
        market_rows=market_rows,
        validation_by_key=validation_by_key,
        violations=violations,
    )
    strict_invalid_rows = sum(1 for reasons in validation_by_key.values() if reasons)
    return OHLCToleranceAuditResult(
        source_name=source_name,
        requested_tickers=fetch_result.requested_tickers,
        fetched_tickers=fetched_tickers,
        missing_tickers=missing_tickers,
        provider_warning=provider_warning,
        provider_error=provider_error,
        total_rows=len(market_rows),
        strict_invalid_rows=strict_invalid_rows,
        violation_count=len(violations),
        violation_reason_counts=violation_reason_counts,
        ticker_summaries=ticker_summaries,
        violations=violations,
        scenarios=scenarios,
        recommendation=_build_recommendation(
            provider_error=provider_error,
            total_rows=len(market_rows),
            validation_by_key=validation_by_key,
            violations=violations,
        ),
        ticker_statuses=fetch_result.ticker_statuses,
        generated_at_utc=generated_at_utc,
    )


def write_ohlc_tolerance_audit_outputs(*, result: OHLCToleranceAuditResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'ohlc_tolerance_audit_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'ohlc_tolerance_audit_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')
    _write_csv(out_dir / 'ohlc_tolerance_violations.csv', [row.to_dict() for row in result.violations])
    _write_csv(out_dir / 'ohlc_tolerance_by_ticker.csv', [row.to_dict() for row in result.ticker_summaries])
    _write_csv(out_dir / 'ohlc_tolerance_scenarios.csv', [row.to_dict() for row in result.scenarios])


def _build_violations_for_row(row: MarketDataRow, reasons: tuple[str, ...]) -> tuple[OHLCToleranceViolation, ...]:
    return tuple(
        OHLCToleranceViolation(
            ticker=row.ticker,
            price_date=row.price_date,
            reason=reason,
            raw_open=row.raw_open,
            raw_high=row.raw_high,
            raw_low=row.raw_low,
            raw_close=row.raw_close,
            adjusted_close=row.adjusted_close,
            absolute_violation_amount=_absolute_violation_amount(row, reason),
            relative_violation_amount_vs_raw_close=_relative_violation_amount(row, reason),
            adjusted_close_differs_from_raw_close=row.raw_close is not None and row.adjusted_close != row.raw_close,
            source=row.data_source,
        )
        for reason in reasons
    )


def _absolute_violation_amount(row: MarketDataRow, reason: str) -> float | None:
    pairs = {
        'raw_low_higher_than_raw_close': (row.raw_low, row.raw_close),
        'raw_low_higher_than_raw_open': (row.raw_low, row.raw_open),
        'raw_high_lower_than_raw_close': (row.raw_close, row.raw_high),
        'raw_high_lower_than_raw_open': (row.raw_open, row.raw_high),
        'raw_high_lower_than_raw_low': (row.raw_low, row.raw_high),
    }
    pair = pairs.get(reason)
    if pair is None or pair[0] is None or pair[1] is None:
        return None
    return abs(float(pair[0]) - float(pair[1]))


def _relative_violation_amount(row: MarketDataRow, reason: str) -> float | None:
    amount = _absolute_violation_amount(row, reason)
    if amount is None or row.raw_close is None or row.raw_close == 0:
        return None
    return amount / abs(float(row.raw_close))


def _evaluate_scenario(
    *,
    scenario_name: str,
    absolute_tolerance: float | None,
    relative_tolerance: float | None,
    market_rows: tuple[MarketDataRow, ...],
    validation_by_key: dict[tuple[str, str, str], tuple[str, ...]],
) -> OHLCToleranceScenario:
    strict_invalid_rows = 0
    tolerated_invalid_rows = 0
    remaining_invalid_rows = 0
    affected_tickers: set[str] = set()
    sntia_result: bool | None = None
    for row in market_rows:
        reasons = validation_by_key[row.key()]
        if not reasons:
            continue
        strict_invalid_rows += 1
        would_pass = _row_would_pass_with_tolerance(
            row=row,
            reasons=reasons,
            absolute_tolerance=absolute_tolerance,
            relative_tolerance=relative_tolerance,
        )
        if would_pass:
            tolerated_invalid_rows += 1
        else:
            remaining_invalid_rows += 1
            affected_tickers.add(row.ticker)
        if row.ticker == 'SNTIA.OL' and row.price_date == '2026-09-09':
            sntia_result = would_pass
    return OHLCToleranceScenario(
        scenario=scenario_name,
        total_rows=len(market_rows),
        strict_invalid_rows=strict_invalid_rows,
        tolerated_invalid_rows=tolerated_invalid_rows,
        remaining_invalid_rows=remaining_invalid_rows,
        affected_tickers=tuple(sorted(affected_tickers)),
        sntia_2026_09_09_would_pass=sntia_result,
    )


def _row_would_pass_with_tolerance(
    *,
    row: MarketDataRow,
    reasons: tuple[str, ...],
    absolute_tolerance: float | None,
    relative_tolerance: float | None,
) -> bool:
    if absolute_tolerance is None and relative_tolerance is None:
        return not reasons
    for reason in reasons:
        if reason not in TOLERABLE_BOUNDARY_REASONS:
            return False
        amount = _absolute_violation_amount(row, reason)
        relative_amount = _relative_violation_amount(row, reason)
        abs_ok = absolute_tolerance is not None and amount is not None and amount <= absolute_tolerance
        rel_ok = relative_tolerance is not None and relative_amount is not None and relative_amount <= relative_tolerance
        if not (abs_ok or rel_ok):
            return False
    return True


def _build_ticker_summaries(
    *,
    market_rows: tuple[MarketDataRow, ...],
    validation_by_key: dict[tuple[str, str, str], tuple[str, ...]],
    violations: tuple[OHLCToleranceViolation, ...],
) -> tuple[OHLCToleranceTickerSummary, ...]:
    row_counts = Counter(row.ticker for row in market_rows)
    invalid_counts = Counter(row.ticker for row in market_rows if validation_by_key[row.key()])
    violation_counts = Counter(violation.ticker for violation in violations)
    by_ticker: dict[str, list[OHLCToleranceViolation]] = {}
    for violation in violations:
        by_ticker.setdefault(violation.ticker, []).append(violation)
    return tuple(
        OHLCToleranceTickerSummary(
            ticker=ticker,
            total_rows=row_counts[ticker],
            strict_invalid_rows=invalid_counts[ticker],
            violation_count=violation_counts[ticker],
            max_absolute_violation_amount=_max_or_none(
                violation.absolute_violation_amount for violation in by_ticker.get(ticker, ())
            ),
            max_relative_violation_amount_vs_raw_close=_max_or_none(
                violation.relative_violation_amount_vs_raw_close for violation in by_ticker.get(ticker, ())
            ),
        )
        for ticker in sorted(row_counts)
    )


def _build_recommendation(
    *,
    provider_error: str | None,
    total_rows: int,
    validation_by_key: dict[tuple[str, str, str], tuple[str, ...]],
    violations: tuple[OHLCToleranceViolation, ...],
) -> str:
    if provider_error:
        return 'blocked_provider_unavailable'
    if not violations:
        return 'keep_strict_ohlc_validation'
    invalid_rows = sum(1 for reasons in validation_by_key.values() if reasons)
    all_tiny = all(
        violation.reason in TOLERABLE_BOUNDARY_REASONS
        and violation.absolute_violation_amount is not None
        and violation.absolute_violation_amount <= 0.10
        and violation.relative_violation_amount_vs_raw_close is not None
        and violation.relative_violation_amount_vs_raw_close <= 0.0025
        for violation in violations
    )
    isolated = total_rows > 0 and invalid_rows <= max(3, int(total_rows * 0.01))
    if all_tiny and isolated:
        return 'apply_conservative_ohlc_tolerance'
    if any(violation.reason in {'raw_high_lower_than_raw_low'} or violation.reason.startswith('nonpositive_') for violation in violations):
        return 'keep_strict_ohlc_validation'
    return 'needs_manual_inspection'


def _render_summary_markdown(result: OHLCToleranceAuditResult) -> str:
    lines = [
        '# OHLC Tolerance Audit Summary',
        '',
        '## Scope',
        '',
        f'- Source: {result.source_name}',
        f'- Requested tickers: {", ".join(result.requested_tickers) or "none"}',
        f'- Fetched tickers: {", ".join(result.fetched_tickers) or "none"}',
        f'- Missing tickers: {", ".join(result.missing_tickers) or "none"}',
        '- No DB path was accepted or required.',
        '- No DB files, market refresh writes, ranking, ML, or Holdings output were produced.',
        '',
        '## Strict validation',
        '',
        f'- Total rows: {result.total_rows}',
        f'- Strict invalid rows: {result.strict_invalid_rows}',
        f'- Violation count: {result.violation_count}',
        f'- Recommendation: {result.recommendation}',
        '',
        '## Violation reasons',
        '',
    ]
    if result.violation_reason_counts:
        for reason, count in result.violation_reason_counts.items():
            lines.append(f'- {reason}: {count}')
    else:
        lines.append('- none')
    return '\n'.join(lines) + '\n'


def _max_or_none(values) -> float | None:
    clean_values = [value for value in values if value is not None]
    return None if not clean_values else max(clean_values)


def _write_csv(path: Path, rows) -> None:
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

from __future__ import annotations

import csv
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from tradetool.diagnostics.candidate_quality_comparison import DEFAULT_REFERENCE_DB_PATH
from tradetool.diagnostics.market_data_v2_readiness import build_market_data_v2_readiness
from tradetool.diagnostics.incumbent_baseline import BASELINE_ID
from tradetool.diagnostics.holdout_snapshot import load_stock_tickers

EXPECTED_REPORT_FILES = (
    'incumbent_screener_eligible_universe.csv',
    'incumbent_screener_rejections.csv',
    'incumbent_screener_summary.json',
    'incumbent_screener_summary.md',
    'incumbent_screener_top_candidates.csv',
)


@dataclass(frozen=True, slots=True)
class IncumbentScreenerResult:
    baseline_id: str
    universe_id: str
    universe_source: str
    requested_stock_ticker_count: int
    benchmark_ticker: str
    as_of_date: str
    effective_feature_date: str | None
    data_source: str
    close_input_source: str
    top_n: int
    selected_count: int
    eligible_count: int
    rejected_count: int
    top_candidates: tuple[dict[str, object], ...]
    eligible_universe: tuple[dict[str, object], ...]
    rejections: tuple[dict[str, object], ...]
    rejection_reason_counts: dict[str, int]
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'baseline_id': self.baseline_id,
            'universe_id': self.universe_id,
            'universe_source': self.universe_source,
            'requested_stock_ticker_count': self.requested_stock_ticker_count,
            'benchmark_ticker': self.benchmark_ticker,
            'as_of_date': self.as_of_date,
            'effective_feature_date': self.effective_feature_date,
            'data_source': self.data_source,
            'close_input_source': self.close_input_source,
            'top_n': self.top_n,
            'selected_count': self.selected_count,
            'eligible_count': self.eligible_count,
            'rejected_count': self.rejected_count,
            'rejection_reason_counts': dict(self.rejection_reason_counts),
            'selection_rule': 'feature-complete stocks sorted by relative_strength_6m descending, ticker ascending',
            'top_candidates': list(self.top_candidates),
            'output_files': list(EXPECTED_REPORT_FILES),
            'guardrails': {
                'screener_ui_changed': False,
                'ml_or_holdings_logic_changed': False,
                'production_database_write_required': False,
            },
            'generated_at_utc': self.generated_at_utc,
        }


def select_incumbent_candidates(rows: tuple[dict[str, object], ...], *, top_n: int = 10) -> tuple[dict[str, object], ...]:
    eligible = tuple(row for row in rows if _as_optional_float(row.get('relative_strength_6m')) is not None)
    ranked = sorted(eligible, key=lambda row: (-_as_float(row['relative_strength_6m']), str(row['ticker'])))
    output: list[dict[str, object]] = []
    for index, row in enumerate(ranked, start=1):
        output.append(
            {
                **row,
                'incumbent_rank': index,
                'incumbent_selected': index <= top_n,
                'incumbent_selection_reason': 'selected_top_n_relative_strength_6m' if index <= top_n else 'outside_top_n_relative_strength_6m',
            }
        )
    return tuple(output)


def build_incumbent_screener(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str,
    as_of_date: date,
    data_source: str,
    top_n: int = 10,
    universe_db_path: str | Path = DEFAULT_REFERENCE_DB_PATH,
) -> IncumbentScreenerResult:
    stock_tickers, universe_source = load_stock_tickers(
        universe_id=universe_id,
        universe_db_path=universe_db_path,
        benchmark_ticker=benchmark_ticker,
    )
    readiness = build_market_data_v2_readiness(
        db_path=db_path,
        tickers=list(stock_tickers),
        benchmark_ticker=benchmark_ticker,
        data_source=data_source,
        max_price_date=as_of_date,
    )
    candidate_rows = tuple(
        _candidate_row(row)
        for row in readiness.feature_rows
        if row.feature_complete
    )
    ranked_rows = select_incumbent_candidates(candidate_rows, top_n=top_n)
    top_candidates = tuple(row for row in ranked_rows if row['incumbent_selected'])
    rejections = _rejection_rows(readiness=readiness, ranked_rows=ranked_rows)
    return IncumbentScreenerResult(
        baseline_id=BASELINE_ID,
        universe_id=universe_id,
        universe_source=universe_source,
        requested_stock_ticker_count=len(stock_tickers),
        benchmark_ticker=benchmark_ticker.strip().upper(),
        as_of_date=as_of_date.isoformat(),
        effective_feature_date=_max_feature_date(ranked_rows),
        data_source=data_source,
        close_input_source='adjusted_close',
        top_n=top_n,
        selected_count=len(top_candidates),
        eligible_count=len(ranked_rows),
        rejected_count=len(rejections),
        top_candidates=top_candidates,
        eligible_universe=ranked_rows,
        rejections=rejections,
        rejection_reason_counts=dict(Counter(str(row['rejection_reason']) for row in rejections)),
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_incumbent_screener_outputs(*, result: IncumbentScreenerResult, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'incumbent_screener_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'incumbent_screener_summary.md').write_text(_render_markdown(result), encoding='utf-8')
    _write_csv(path / 'incumbent_screener_top_candidates.csv', result.top_candidates)
    _write_csv(path / 'incumbent_screener_eligible_universe.csv', result.eligible_universe)
    _write_csv(path / 'incumbent_screener_rejections.csv', result.rejections)


def _candidate_row(row) -> dict[str, object]:
    features = dict(row.features)
    return {
        'ticker': row.ticker,
        'latest_feature_date': row.feature_date,
        'latest_price_date': row.latest_price_date,
        'close': features.get('latest_close'),
        'relative_strength_6m': features.get('relative_strength_6m'),
        'relative_strength_3m': features.get('relative_strength_3m'),
        'return_6m': features.get('return_6m'),
        'return_3m': features.get('return_3m'),
        'above_sma200': features.get('above_sma200'),
        'average_traded_value_20': features.get('average_traded_value_20'),
        'drawdown_252': features.get('drawdown_252'),
    }


def _rejection_rows(*, readiness, ranked_rows: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
    ranked_tickers = {str(row['ticker']) for row in ranked_rows}
    output: list[dict[str, object]] = []
    for row in readiness.feature_rows:
        if row.ticker in ranked_tickers:
            continue
        output.append(
            {
                'ticker': row.ticker,
                'latest_feature_date': row.feature_date,
                'rejection_reason': 'feature_incomplete',
                'rejection_detail': '|'.join(row.feature_missing_reasons),
            }
        )
    present = {row.ticker for row in readiness.feature_rows}
    for row in readiness.coverage_rows:
        if row.is_benchmark or row.ticker in present:
            continue
        output.append(
            {
                'ticker': row.ticker,
                'latest_feature_date': '',
                'rejection_reason': 'missing_market_data',
                'rejection_detail': 'missing_price_history_v2_rows',
            }
        )
    return tuple(sorted(output, key=lambda row: str(row['ticker'])))


def _max_feature_date(rows: tuple[dict[str, object], ...]) -> str | None:
    dates = [str(row['latest_feature_date']) for row in rows if row.get('latest_feature_date')]
    return max(dates) if dates else None


def _render_markdown(result: IncumbentScreenerResult) -> str:
    lines = [
        '# Incumbent Screener Summary',
        '',
        f'- Baseline ID: `{result.baseline_id}`',
        f'- Universe: `{result.universe_id}`',
        f'- Universe source: `{result.universe_source}`',
        f'- Benchmark: `{result.benchmark_ticker}`',
        f'- As-of date: `{result.as_of_date}`',
        f'- Effective feature date: `{result.effective_feature_date or "none"}`',
        f'- Close input source: `{result.close_input_source}`',
        f'- Selection rule: feature-complete stocks sorted by `relative_strength_6m` descending, ticker ascending',
        f'- Selected: {result.selected_count}/{result.top_n}',
        f'- Eligible universe: {result.eligible_count}',
        f'- Rejected: {result.rejected_count}',
        '',
        '## Top Candidates',
    ]
    for row in result.top_candidates:
        lines.append(f"- {row['incumbent_rank']}. {row['ticker']} rs6m={row.get('relative_strength_6m')} rs3m={row.get('relative_strength_3m')}")
    if not result.top_candidates:
        lines.append('- none')
    lines.extend(['', '## Guardrails', '- Reusable screener core only; no UI, ML, Holdings, or production DB write change.'])
    return '\n'.join(lines) + '\n'


def _write_csv(path: Path, rows: tuple[dict[str, object], ...]) -> None:
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

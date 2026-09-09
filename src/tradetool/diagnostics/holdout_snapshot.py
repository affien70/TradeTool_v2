from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from tradetool.data import ReadOnlySQLite
from tradetool.diagnostics.candidate_quality_comparison import DEFAULT_REFERENCE_DB_PATH
from tradetool.diagnostics.market_data_v2_readiness import MarketDataV2ReadinessResult, build_market_data_v2_readiness
from tradetool.policy import (
    CANDIDATE_TYPE_ENGINE_ID,
    CandidateTypeInputRow,
    TRADE_POLICY_ENGINE_ID,
    TradePolicyInputRow,
    apply_candidate_type_diagnostics,
    apply_trade_policy_diagnostics,
    summarize_candidate_types,
)
from tradetool.ranking import BASELINE_RANKING_ENGINE_ID, BaselineRankingInput, build_baseline_ranking
from tradetool.universe.tickers import load_universe_tickers

PRICE_TABLE_V2 = 'price_history_v2'
EXPECTED_REPORT_FILES = (
    'holdout_snapshot_summary.json',
    'holdout_snapshot_summary.md',
    'snapshot_candidate_type_counts.csv',
    'snapshot_data_coverage.csv',
    'snapshot_ranked_candidates.csv',
    'snapshot_rejections.csv',
    'snapshot_signal_counts.csv',
)


@dataclass(frozen=True, slots=True)
class HoldoutSnapshotResult:
    universe_id: str
    universe_source: str
    requested_stock_ticker_count: int
    as_of_date: str
    effective_feature_date: str | None
    benchmark_ticker: str
    benchmark_present: bool
    benchmark_latest_date: str | None
    data_source: str
    close_input_source: str
    snapshot_valid: bool
    invalid_reasons: tuple[str, ...]
    present_ticker_count: int
    missing_ticker_count: int
    missing_tickers: tuple[str, ...]
    feature_complete_count: int
    feature_incomplete_count: int
    ranked_count: int
    signal_counts: dict[str, int]
    candidate_type_counts: dict[str, int]
    coverage_rows: tuple[dict[str, object], ...]
    ranked_rows: tuple[dict[str, object], ...]
    rejection_rows: tuple[dict[str, object], ...]
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'universe_id': self.universe_id,
            'universe_source': self.universe_source,
            'requested_stock_ticker_count': self.requested_stock_ticker_count,
            'as_of_date': self.as_of_date,
            'effective_feature_date': self.effective_feature_date,
            'benchmark_ticker': self.benchmark_ticker,
            'benchmark_present': self.benchmark_present,
            'benchmark_latest_date': self.benchmark_latest_date,
            'data_source': self.data_source,
            'price_table': PRICE_TABLE_V2,
            'close_input_source': self.close_input_source,
            'snapshot_valid': self.snapshot_valid,
            'invalid_reasons': list(self.invalid_reasons),
            'present_ticker_count': self.present_ticker_count,
            'missing_ticker_count': self.missing_ticker_count,
            'missing_tickers': list(self.missing_tickers),
            'feature_complete_count': self.feature_complete_count,
            'feature_incomplete_count': self.feature_incomplete_count,
            'ranked_count': self.ranked_count,
            'signal_counts': dict(self.signal_counts),
            'candidate_type_counts': dict(self.candidate_type_counts),
            'top_20_ranked_candidates': list(self.ranked_rows[:20]),
            'leakage_controls': {
                'price_rows_capped_at_as_of_date': True,
                'benchmark_rows_capped_at_as_of_date': True,
                'forward_returns_calculated': False,
                'ml_score_calculated': False,
                'holdings_adjustment_applied': False,
                'manual_focus_boost_applied': False,
            },
            'generated_at_utc': self.generated_at_utc,
        }


def build_holdout_snapshot(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str,
    as_of_date: date,
    data_source: str,
    universe_db_path: str | Path = DEFAULT_REFERENCE_DB_PATH,
    min_history_rows: int = 252,
) -> HoldoutSnapshotResult:
    stock_tickers, universe_source = load_stock_tickers(universe_id=universe_id, universe_db_path=universe_db_path, benchmark_ticker=benchmark_ticker)
    readiness = build_market_data_v2_readiness(
        db_path=db_path,
        tickers=list(stock_tickers),
        benchmark_ticker=benchmark_ticker,
        data_source=data_source,
        min_history_rows=min_history_rows,
        max_price_date=as_of_date,
    )
    ranked_rows, rejections, signal_counts, type_counts = _rank_snapshot(readiness)
    coverage_rows = tuple(row.to_dict() for row in readiness.coverage_rows)
    missing_tickers = tuple(row['ticker'] for row in coverage_rows if not row['is_benchmark'] and not row['present'])
    feature_dates = [str(row['feature_date']) for row in ranked_rows if row.get('feature_date')]
    invalid_reasons = _invalid_reasons(readiness)
    return HoldoutSnapshotResult(
        universe_id=universe_id,
        universe_source=universe_source,
        requested_stock_ticker_count=len(stock_tickers),
        as_of_date=as_of_date.isoformat(),
        effective_feature_date=max(feature_dates) if feature_dates else None,
        benchmark_ticker=benchmark_ticker.strip().upper(),
        benchmark_present=readiness.benchmark_present,
        benchmark_latest_date=readiness.benchmark_latest_date,
        data_source=data_source,
        close_input_source='adjusted_close',
        snapshot_valid=not invalid_reasons,
        invalid_reasons=invalid_reasons,
        present_ticker_count=sum(1 for row in coverage_rows if not row['is_benchmark'] and row['present']),
        missing_ticker_count=len(missing_tickers),
        missing_tickers=missing_tickers,
        feature_complete_count=readiness.feature_complete_count,
        feature_incomplete_count=readiness.feature_incomplete_count,
        ranked_count=len(ranked_rows),
        signal_counts=signal_counts,
        candidate_type_counts=type_counts,
        coverage_rows=coverage_rows,
        ranked_rows=ranked_rows,
        rejection_rows=rejections,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def load_stock_tickers(*, universe_id: str, universe_db_path: str | Path, benchmark_ticker: str) -> tuple[tuple[str, ...], str]:
    selection = load_universe_tickers(universe_id=universe_id, database=ReadOnlySQLite(universe_db_path))
    benchmark = benchmark_ticker.strip().upper()
    tickers = tuple(ticker for ticker in selection.tickers if ticker.endswith('.OL') and not ticker.startswith('^') and ticker != benchmark)
    if not tickers:
        raise ValueError(f'Universe "{universe_id}" did not produce any .OL stock tickers.')
    return tickers, selection.source


def write_holdout_snapshot_outputs(*, result: HoldoutSnapshotResult, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'holdout_snapshot_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'holdout_snapshot_summary.md').write_text(_render_markdown(result), encoding='utf-8')
    _write_csv(path / 'snapshot_ranked_candidates.csv', result.ranked_rows)
    _write_csv(path / 'snapshot_signal_counts.csv', _count_rows(result.signal_counts, 'trade_signal'))
    _write_csv(path / 'snapshot_candidate_type_counts.csv', _count_rows(result.candidate_type_counts, 'candidate_type'))
    _write_csv(path / 'snapshot_rejections.csv', result.rejection_rows)
    _write_csv(path / 'snapshot_data_coverage.csv', result.coverage_rows)


def _rank_snapshot(readiness: MarketDataV2ReadinessResult) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...], dict[str, int], dict[str, int]]:
    complete_rows = [row for row in readiness.feature_rows if row.feature_complete]
    ranked = build_baseline_ranking(
        tuple(
            BaselineRankingInput(
                ticker=row.ticker,
                rank_date=date.fromisoformat(row.feature_date),
                features=row.features,
            )
            for row in complete_rows
        )
    )
    policy_rows = apply_trade_policy_diagnostics(
        tuple(
            TradePolicyInputRow(
                ticker=row.ranked_candidate.ticker,
                rank_date=row.ranked_candidate.rank_date.isoformat(),
                ranking_engine_id=row.ranked_candidate.ranking_engine_id,
                raw_rank=row.ranked_candidate.raw_rank,
                raw_score=row.ranked_candidate.raw_score,
                input_fields=row.input_fields,
            )
            for row in ranked
        )
    )
    candidate_rows = apply_candidate_type_diagnostics(
        tuple(
            CandidateTypeInputRow(
                ticker=row.ticker,
                rank_date=row.rank_date,
                ranking_engine_id=row.ranking_engine_id,
                policy_engine_id=row.policy_engine_id,
                raw_rank=row.raw_rank,
                raw_score=row.raw_score,
                trade_signal=row.trade_signal,
                policy_pass=row.policy_pass,
                policy_reasons=row.policy_reasons,
                policy_warnings=row.policy_warnings,
                above_sma50=row.above_sma50,
                above_sma200=row.above_sma200,
                positive_return_3m=row.positive_return_3m,
                positive_return_6m=row.positive_return_6m,
                positive_rs_3m=row.positive_rs_3m,
                positive_rs_6m=row.positive_rs_6m,
                acceptable_drawdown=row.acceptable_drawdown,
                acceptable_volatility=row.acceptable_volatility,
                acceptable_traded_value=row.acceptable_traded_value,
                moderate_stretch=row.moderate_stretch,
                severe_stretch=row.severe_stretch,
                drawdown_252=row.drawdown_252,
                volatility_63=row.volatility_63,
                average_traded_value_20=row.average_traded_value_20,
                distance_to_sma50=row.distance_to_sma50,
                distance_to_sma200=row.distance_to_sma200,
            )
            for row in policy_rows
        )
    )
    ranked_output: list[dict[str, object]] = []
    for candidate, policy, ranking in zip(candidate_rows, policy_rows, ranked, strict=True):
        features = ranking.input_fields
        ranked_output.append(
            {
                'ticker': candidate.ticker,
                'feature_date': candidate.rank_date,
                'raw_rank': candidate.raw_rank,
                'raw_score': candidate.raw_score,
                'trade_signal': candidate.trade_signal.value,
                'candidate_type': candidate.candidate_type.value,
                'latest_price_date': features.get('latest_price_date'),
                'latest_close': features.get('latest_close'),
                'return_1m': features.get('return_1m'),
                'return_3m': features.get('return_3m'),
                'return_6m': features.get('return_6m'),
                'relative_strength_3m': features.get('relative_strength_3m'),
                'relative_strength_6m': features.get('relative_strength_6m'),
                'above_sma50': policy.above_sma50,
                'above_sma200': policy.above_sma200,
                'drawdown_252': policy.drawdown_252,
                'volatility_63': policy.volatility_63,
                'average_traded_value_20': policy.average_traded_value_20,
                'policy_reasons': '; '.join(policy.policy_reasons),
                'policy_warnings': '; '.join(policy.policy_warnings),
                'classification_reasons': '; '.join(candidate.classification_reasons),
                'classification_warnings': '; '.join(candidate.classification_warnings),
            }
        )
    rejections = _build_rejection_rows(readiness, ranked_output)
    summary = summarize_candidate_types(candidate_rows)
    return tuple(ranked_output), rejections, dict(summary['trade_signal_counts']), dict(summary['candidate_type_counts'])


def _build_rejection_rows(readiness: MarketDataV2ReadinessResult, ranked_rows: list[dict[str, object]]) -> tuple[dict[str, object], ...]:
    ranked_tickers = {str(row['ticker']) for row in ranked_rows}
    output: list[dict[str, object]] = []
    for row in readiness.feature_rows:
        if row.ticker in ranked_tickers:
            continue
        output.append(
            {
                'ticker': row.ticker,
                'status': 'feature_incomplete',
                'feature_date': row.feature_date,
                'rejection_reasons': '; '.join(row.feature_missing_reasons),
            }
        )
    present = {row.ticker for row in readiness.feature_rows}
    for row in readiness.coverage_rows:
        if row.is_benchmark or row.ticker in present:
            continue
        output.append(
            {
                'ticker': row.ticker,
                'status': 'missing_market_data',
                'feature_date': '',
                'rejection_reasons': 'missing_price_history_v2_rows',
            }
        )
    return tuple(output)


def _invalid_reasons(readiness: MarketDataV2ReadinessResult) -> tuple[str, ...]:
    reasons: list[str] = []
    if not readiness.benchmark_present:
        reasons.append('missing_benchmark_data')
    if readiness.benchmark_ticker and readiness.benchmark_latest_date is None:
        reasons.append('missing_benchmark_latest_date')
    return tuple(reasons)


def _render_markdown(result: HoldoutSnapshotResult) -> str:
    lines = [
        '# Holdout Snapshot Summary',
        '',
        '## Scope',
        f'- Diagnostic snapshot only: `true`',
        f'- Universe source: `{result.universe_source}`',
        f'- Requested stock tickers: {result.requested_stock_ticker_count}',
        f'- As-of date requested: `{result.as_of_date}`',
        f'- Effective feature date: `{result.effective_feature_date or "none"}`',
        f'- Benchmark ticker: `{result.benchmark_ticker}`',
        f'- Benchmark present: `{result.benchmark_present}`',
        f'- Benchmark latest date: `{result.benchmark_latest_date or "missing"}`',
        f'- Close input source: `{result.close_input_source}`',
        '',
        '## Results',
        f'- Snapshot valid: `{result.snapshot_valid}`',
        f'- Invalid reasons: `{list(result.invalid_reasons)}`',
        f'- Present ticker count: {result.present_ticker_count}',
        f'- Missing ticker count: {result.missing_ticker_count}',
        f'- Feature complete count: {result.feature_complete_count}',
        f'- Feature incomplete count: {result.feature_incomplete_count}',
        f'- Ranked count: {result.ranked_count}',
        f'- Signal counts: `{result.signal_counts}`',
        f'- Candidate type counts: `{result.candidate_type_counts}`',
        '',
        '## Leakage Controls',
        '- price_history_v2 rows are capped at `price_date <= as_of_date` for stocks and benchmark.',
        '- No forward returns, ML score, Holdings adjustment, or manual focus boost are calculated.',
        '',
        '## Top 20',
    ]
    for row in result.ranked_rows[:20]:
        lines.append(f"- {row['raw_rank']}. {row['ticker']} score={row['raw_score']} signal={row['trade_signal']} type={row['candidate_type']}")
    if not result.ranked_rows:
        lines.append('- none')
    return '\n'.join(lines) + '\n'


def _count_rows(counts: dict[str, int], field_name: str) -> list[dict[str, object]]:
    return [{field_name: key, 'count': value} for key, value in sorted(counts.items())]


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

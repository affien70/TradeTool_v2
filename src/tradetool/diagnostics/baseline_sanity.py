from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradetool.diagnostics.baseline_ranking import (
    BaselineRankingDiagnosticsResult,
    BaselineRankingRow,
    build_baseline_ranking_diagnostics,
)
from tradetool.diagnostics.eligibility import build_eligibility_diagnostics
from tradetool.diagnostics.feature_readiness import build_feature_readiness_diagnostics

POSITIVE_FOCUS_TICKERS = (
    'SUBC.OL',
    'NHY.OL',
    'AKRBP.OL',
    'HAUTO.OL',
    'MPCC.OL',
    'FRO.OL',
    'KIT.OL',
    'ENDUR.OL',
    'BWLPG.OL',
    'VAR.OL',
)
BAD_FOCUS_TICKERS = (
    'ZENA.OL',
    'NBX.OL',
    'PRS.OL',
    'BCS.OL',
    'MORLD.OL',
)
DECISION_CONTINUE = 'continue_to_trade_policy'
DECISION_REVISE = 'revise_baseline_formula_later'
DECISION_BLOCKED = 'blocked_insufficient_evidence'


@dataclass(frozen=True, slots=True)
class FocusTickerSanityRow:
    ticker: str
    set_name: str
    present_in_universe: bool
    structurally_eligible: bool
    feature_complete: bool
    raw_rank: int | None
    raw_score: float | None
    note: str
    key_features: Mapping[str, float | int | bool | str | None]

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'set_name': self.set_name,
            'present_in_universe': self.present_in_universe,
            'structurally_eligible': self.structurally_eligible,
            'feature_complete': self.feature_complete,
            'raw_rank': self.raw_rank,
            'raw_score': self.raw_score,
            'note': self.note,
            **self.key_features,
        }


@dataclass(frozen=True, slots=True)
class BadNamePenaltyRow:
    ticker: str
    in_top10: bool
    in_top20: bool
    raw_rank: int | None
    raw_score: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'in_top10': self.in_top10,
            'in_top20': self.in_top20,
            'raw_rank': self.raw_rank,
            'raw_score': self.raw_score,
        }


@dataclass(frozen=True, slots=True)
class BaselineSanityDiagnosticsResult:
    ranking: BaselineRankingDiagnosticsResult
    evidence_dir: Path
    top20_rows: tuple[BaselineRankingRow, ...]
    focus_rows: tuple[FocusTickerSanityRow, ...]
    bad_name_rows: tuple[BadNamePenaltyRow, ...]
    positive_top10_count: int
    positive_top20_count: int
    bad_top10_count: int
    bad_top20_count: int
    comparison_summary: Mapping[str, object]
    comparison_limitations: tuple[str, ...]
    decision: str
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'universe_id': self.ranking.universe_id,
            'universe_source': self.ranking.universe_source,
            'benchmark_ticker': self.ranking.benchmark_ticker,
            'ranking_engine_id': self.ranking.ranking_engine_id,
            'input_universe_count': self.ranking.input_universe_count,
            'structural_eligible_count': self.ranking.structural_eligible_count,
            'structural_rejected_count': self.ranking.structural_rejected_count,
            'feature_complete_count': self.ranking.feature_complete_count,
            'ranked_count': self.ranking.ranked_count,
            'max_price_date': self._max_price_date(),
            'generated_at_utc': self.generated_at_utc,
            'positive_manual_top10_count': self.positive_top10_count,
            'positive_manual_top20_count': self.positive_top20_count,
            'bad_name_top10_count': self.bad_top10_count,
            'bad_name_top20_count': self.bad_top20_count,
            'comparison_summary': dict(self.comparison_summary),
            'comparison_limitations': list(self.comparison_limitations),
            'decision': self.decision,
        }

    def _max_price_date(self) -> str | None:
        dates = [
            row.input_fields.get('latest_price_date')
            for row in self.ranking.rows
            if isinstance(row.input_fields.get('latest_price_date'), str)
        ]
        return max(dates) if dates else None


def build_baseline_sanity_diagnostics(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str | None,
    evidence_dir: str | Path,
    explicit_tickers: Sequence[str] | None = None,
    universe_csv_path: str | Path | None = None,
    price_table: str = 'price_history',
    min_history_rows: int = 252,
    freshness_tolerance_days: int = 0,
) -> BaselineSanityDiagnosticsResult:
    ranking = build_baseline_ranking_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        benchmark_ticker=benchmark_ticker,
        explicit_tickers=explicit_tickers,
        universe_csv_path=universe_csv_path,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    eligibility = build_eligibility_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        explicit_tickers=explicit_tickers,
        universe_csv_path=universe_csv_path,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    feature_readiness = build_feature_readiness_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        benchmark_ticker=benchmark_ticker,
        explicit_tickers=explicit_tickers,
        universe_csv_path=universe_csv_path,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    top20_rows = ranking.rows[:20]
    focus_rows = _build_focus_rows(ranking=ranking, eligibility=eligibility, feature_readiness=feature_readiness)
    bad_name_rows = _build_bad_name_rows(ranking)
    positive_top10_count = _count_focus_hits(ranking, POSITIVE_FOCUS_TICKERS, top_n=10)
    positive_top20_count = _count_focus_hits(ranking, POSITIVE_FOCUS_TICKERS, top_n=20)
    bad_top10_count = _count_focus_hits(ranking, BAD_FOCUS_TICKERS, top_n=10)
    bad_top20_count = _count_focus_hits(ranking, BAD_FOCUS_TICKERS, top_n=20)
    resolved_evidence_dir = Path(evidence_dir).expanduser().resolve()
    comparison_summary, comparison_limitations = _build_phase1_comparison(
        evidence_dir=resolved_evidence_dir,
        top20_tickers=tuple(row.ticker for row in top20_rows),
    )
    decision = _recommend_decision(
        bad_top20_count=bad_top20_count,
        comparison_available=not comparison_limitations,
    )
    return BaselineSanityDiagnosticsResult(
        ranking=ranking,
        evidence_dir=resolved_evidence_dir,
        top20_rows=top20_rows,
        focus_rows=focus_rows,
        bad_name_rows=bad_name_rows,
        positive_top10_count=positive_top10_count,
        positive_top20_count=positive_top20_count,
        bad_top10_count=bad_top10_count,
        bad_top20_count=bad_top20_count,
        comparison_summary=comparison_summary,
        comparison_limitations=comparison_limitations,
        decision=decision,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_baseline_sanity_outputs(*, result: BaselineSanityDiagnosticsResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / 'baseline_top20.csv', [row.to_dict() for row in result.top20_rows])
    _write_csv(out_dir / 'focus_ticker_sanity.csv', [row.to_dict() for row in result.focus_rows])
    _write_csv(out_dir / 'bad_name_penalty.csv', [row.to_dict() for row in result.bad_name_rows])
    (out_dir / 'baseline_sanity_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'baseline_sanity_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')


def _build_focus_rows(*, ranking, eligibility, feature_readiness) -> tuple[FocusTickerSanityRow, ...]:
    ranked_by_ticker = {row.ticker: row for row in ranking.rows}
    eligibility_by_ticker = {row.ticker: row for row in eligibility.results}
    feature_by_ticker = {row.ticker: row for row in feature_readiness.rows}
    universe_tickers = set(eligibility_by_ticker)
    rows: list[FocusTickerSanityRow] = []
    for set_name, tickers in (('positive_manual', POSITIVE_FOCUS_TICKERS), ('bad_high_risk', BAD_FOCUS_TICKERS)):
        for ticker in tickers:
            ranked = ranked_by_ticker.get(ticker)
            eligibility_row = eligibility_by_ticker.get(ticker)
            feature_row = feature_by_ticker.get(ticker)
            present = ticker in universe_tickers
            note = _focus_note(
                ticker=ticker,
                present=present,
                eligibility_row=eligibility_row,
                feature_row=feature_row,
                ranked=ranked,
                set_name=set_name,
            )
            key_features: dict[str, float | int | bool | str | None] = {}
            if feature_row is not None:
                key_features = {
                    'relative_strength_3m': feature_row.features.get('relative_strength_3m'),
                    'relative_strength_6m': feature_row.features.get('relative_strength_6m'),
                    'return_3m': feature_row.features.get('return_3m'),
                    'return_6m': feature_row.features.get('return_6m'),
                    'drawdown_252': feature_row.features.get('drawdown_252'),
                    'volatility_63': feature_row.features.get('volatility_63'),
                    'average_traded_value_20': feature_row.features.get('average_traded_value_20'),
                }
            rows.append(
                FocusTickerSanityRow(
                    ticker=ticker,
                    set_name=set_name,
                    present_in_universe=present,
                    structurally_eligible=bool(eligibility_row and eligibility_row.eligible),
                    feature_complete=bool(feature_row and feature_row.feature_complete),
                    raw_rank=None if ranked is None else ranked.raw_rank,
                    raw_score=None if ranked is None else ranked.raw_score,
                    note=note,
                    key_features=key_features,
                )
            )
    return tuple(rows)


def _build_bad_name_rows(ranking: BaselineRankingDiagnosticsResult) -> tuple[BadNamePenaltyRow, ...]:
    ranked_by_ticker = {row.ticker: row for row in ranking.rows}
    rows: list[BadNamePenaltyRow] = []
    for ticker in BAD_FOCUS_TICKERS:
        ranked = ranked_by_ticker.get(ticker)
        raw_rank = None if ranked is None else ranked.raw_rank
        rows.append(
            BadNamePenaltyRow(
                ticker=ticker,
                in_top10=raw_rank is not None and raw_rank <= 10,
                in_top20=raw_rank is not None and raw_rank <= 20,
                raw_rank=raw_rank,
                raw_score=None if ranked is None else ranked.raw_score,
            )
        )
    return tuple(rows)


def _build_phase1_comparison(*, evidence_dir: Path, top20_tickers: Sequence[str]) -> tuple[dict[str, object], tuple[str, ...]]:
    comparison_file = evidence_dir / 'ose_method_comparison_top20.csv'
    if not comparison_file.exists():
        return (
            {},
            ('Phase 1 top-20 comparison CSV is not present in the copied evidence directory.',),
        )
    with comparison_file.open('r', encoding='utf-8', newline='') as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return ({}, ('Phase 1 top-20 comparison CSV is empty.',))
    comparisons: dict[str, object] = {}
    for method in ('main_momentum', 'raw_ml', 'parked_practical_first'):
        tickers = [row['ticker'] for row in rows if row.get('method') == method and row.get('ticker')]
        if tickers:
            overlap = sorted(set(top20_tickers).intersection(tickers))
            comparisons[method] = {
                'phase1_top20_count': len(tickers),
                'overlap_count': len(overlap),
                'overlap_tickers': overlap,
            }
    if not comparisons:
        return ({}, ('Phase 1 comparison rows were present but no supported OSE methods were found.',))
    return comparisons, ()


def _recommend_decision(*, bad_top20_count: int, comparison_available: bool) -> str:
    if not comparison_available:
        return DECISION_BLOCKED
    if bad_top20_count > 0:
        return DECISION_REVISE
    return DECISION_CONTINUE


def _count_focus_hits(ranking: BaselineRankingDiagnosticsResult, focus_set: Sequence[str], *, top_n: int) -> int:
    focus = set(focus_set)
    return sum(1 for row in ranking.rows if row.raw_rank <= top_n and row.ticker in focus)


def _focus_note(*, ticker, present, eligibility_row, feature_row, ranked, set_name) -> str:
    if not present:
        return 'ticker is not present in the requested universe'
    if eligibility_row is not None and not eligibility_row.eligible:
        reasons = ', '.join(eligibility_row.rejection_reasons) or 'structural rejection'
        return f'structurally rejected: {reasons}'
    if feature_row is not None and not feature_row.feature_complete:
        reasons = ', '.join(feature_row.feature_missing_reasons) or 'feature incomplete'
        return f'feature incomplete: {reasons}'
    if ranked is None:
        return 'present and feature-complete but not ranked'
    if set_name == 'bad_high_risk' and ranked.raw_rank <= 20:
        return 'bad/high-risk ticker appears in top 20'
    if set_name == 'positive_manual' and ranked.raw_rank <= 20:
        return 'positive/manual ticker appears in top 20'
    return 'present in ranked set outside top 20'


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = ['ticker']
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _render_summary_markdown(result: BaselineSanityDiagnosticsResult) -> str:
    lines = [
        '# Baseline Ranking Sanity Summary',
        '',
        '## Run metadata',
        '',
        f'- Universe id: {result.ranking.universe_id}',
        f'- Universe source: {result.ranking.universe_source}',
        f'- Benchmark ticker: {result.ranking.benchmark_ticker or "not requested"}',
        f'- Ranking engine id: {result.ranking.ranking_engine_id}',
        f'- Input universe count: {result.ranking.input_universe_count}',
        f'- Structural eligible count: {result.ranking.structural_eligible_count}',
        f'- Structural rejected count: {result.ranking.structural_rejected_count}',
        f'- Feature-complete count: {result.ranking.feature_complete_count}',
        f'- Ranked count: {result.ranking.ranked_count}',
        f'- Max price date: {result.to_summary_dict().get("max_price_date") or "n/a"}',
        f'- Generated at: {result.generated_at_utc}',
        '',
        '## Useful-candidate coverage',
        '',
        f'- Positive/manual in top 10: {result.positive_top10_count}',
        f'- Positive/manual in top 20: {result.positive_top20_count}',
        '',
        '## Bad-name penalty',
        '',
        f'- Bad/high-risk in top 10: {result.bad_top10_count}',
        f'- Bad/high-risk in top 20: {result.bad_top20_count}',
        '',
        '## Comparison to Phase 1 evidence',
        '',
    ]
    if result.comparison_limitations:
        for limitation in result.comparison_limitations:
            lines.append(f'- Limitation: {limitation}')
    else:
        for method, summary in result.comparison_summary.items():
            tickers = ', '.join(summary['overlap_tickers']) or 'none'
            lines.append(
                f"- {method}: overlap {summary['overlap_count']}/{summary['phase1_top20_count']} ({tickers})"
            )
    lines.extend(['', '## Decision', '', f'- {result.decision}', ''])
    return '\n'.join(lines)

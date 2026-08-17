from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradetool.contracts.enums import CandidateType, TradeSignal
from tradetool.diagnostics.candidate_type import CandidateTypeDiagnosticsResult, build_candidate_type_diagnostics
from tradetool.diagnostics.eligibility import build_eligibility_diagnostics
from tradetool.diagnostics.feature_readiness import build_feature_readiness_diagnostics

FOCUS_TICKERS = (
    'KIT.OL',
    'HAUTO.OL',
    'MPCC.OL',
    'SUBC.OL',
    'FRO.OL',
    'BWLPG.OL',
    'ENDUR.OL',
    'VAR.OL',
    'NHY.OL',
    'AKRBP.OL',
)
BAD_HIGH_RISK_TICKERS = (
    'ZENA.OL',
    'NBX.OL',
    'PRS.OL',
    'BCS.OL',
    'MORLD.OL',
)
DECISION_CONTINUE = 'continue_to_minimal_screener_ui'
DECISION_REVISE_CANDIDATE_TYPES = 'revise_candidate_type_rules'
DECISION_REVISE_TRADE_POLICY = 'revise_trade_policy_first'
DECISION_BLOCKED = 'blocked_insufficient_evidence'
GATE_FIELDS = (
    'above_sma50',
    'above_sma200',
    'positive_return_3m',
    'positive_return_6m',
    'positive_rs_3m',
    'positive_rs_6m',
    'acceptable_drawdown',
    'acceptable_volatility',
    'acceptable_traded_value',
    'moderate_stretch',
)


@dataclass(frozen=True, slots=True)
class DistributionRow:
    distribution: str
    name: str
    count: int
    percent: float

    def to_dict(self) -> dict[str, object]:
        return {
            'distribution': self.distribution,
            'name': self.name,
            'count': self.count,
            'percent': round(self.percent, 4),
        }


@dataclass(frozen=True, slots=True)
class SignalMatrixRow:
    candidate_type: str
    trade_signal: str
    count: int
    percent_of_total: float

    def to_dict(self) -> dict[str, object]:
        return {
            'candidate_type': self.candidate_type,
            'trade_signal': self.trade_signal,
            'count': self.count,
            'percent_of_total': round(self.percent_of_total, 4),
        }


@dataclass(frozen=True, slots=True)
class Top20CandidateTypeAuditRow:
    ticker: str
    raw_rank: int
    raw_score: float
    trade_signal: str
    candidate_type: str
    classification_reasons: tuple[str, ...]
    classification_warnings: tuple[str, ...]
    policy_reasons: tuple[str, ...]
    policy_warnings: tuple[str, ...]
    diagnostic_note: str

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'raw_rank': self.raw_rank,
            'raw_score': self.raw_score,
            'trade_signal': self.trade_signal,
            'candidate_type': self.candidate_type,
            'classification_reasons': '; '.join(self.classification_reasons),
            'classification_warnings': '; '.join(self.classification_warnings),
            'policy_reasons': '; '.join(self.policy_reasons),
            'policy_warnings': '; '.join(self.policy_warnings),
            'diagnostic_note': self.diagnostic_note,
        }


@dataclass(frozen=True, slots=True)
class FocusCandidateTypeAuditRow:
    ticker: str
    present_in_universe: bool
    raw_rank: int | None
    trade_signal: str | None
    candidate_type: str | None
    failed_gates: tuple[str, ...]
    policy_reasons: tuple[str, ...]
    policy_warnings: tuple[str, ...]
    classification_reasons: tuple[str, ...]
    classification_warnings: tuple[str, ...]
    diagnostic_note: str

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'present_in_universe': self.present_in_universe,
            'raw_rank': self.raw_rank,
            'trade_signal': self.trade_signal,
            'candidate_type': self.candidate_type,
            'failed_gates': '; '.join(self.failed_gates),
            'policy_reasons': '; '.join(self.policy_reasons),
            'policy_warnings': '; '.join(self.policy_warnings),
            'classification_reasons': '; '.join(self.classification_reasons),
            'classification_warnings': '; '.join(self.classification_warnings),
            'diagnostic_note': self.diagnostic_note,
        }


@dataclass(frozen=True, slots=True)
class BadHighRiskAuditRow:
    ticker: str
    present_in_universe: bool
    raw_rank: int | None
    trade_signal: str | None
    candidate_type: str | None
    in_top20: bool
    diagnostic_note: str

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'present_in_universe': self.present_in_universe,
            'raw_rank': self.raw_rank,
            'trade_signal': self.trade_signal,
            'candidate_type': self.candidate_type,
            'in_top20': self.in_top20,
            'diagnostic_note': self.diagnostic_note,
        }


@dataclass(frozen=True, slots=True)
class CandidateTypeSanityResult:
    candidate: CandidateTypeDiagnosticsResult
    top20_rows: tuple[Top20CandidateTypeAuditRow, ...]
    focus_rows: tuple[FocusCandidateTypeAuditRow, ...]
    bad_high_risk_rows: tuple[BadHighRiskAuditRow, ...]
    distribution_rows: tuple[DistributionRow, ...]
    matrix_rows: tuple[SignalMatrixRow, ...]
    warnings: tuple[str, ...]
    decision_recommendation: str
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'universe_id': self.candidate.policy.ranking.universe_id,
            'universe_source': self.candidate.policy.ranking.universe_source,
            'benchmark_ticker': self.candidate.policy.ranking.benchmark_ticker,
            'ranking_engine_id': self.candidate.policy.ranking.ranking_engine_id,
            'policy_engine_id': self.candidate.policy.policy_engine_id,
            'classification_engine_id': self.candidate.classification_engine_id,
            'input_universe_count': self.candidate.policy.ranking.input_universe_count,
            'ranked_count': self.candidate.policy.ranking.ranked_count,
            'candidate_type_row_count': len(self.candidate.rows),
            'policy_row_count': len(self.candidate.policy.rows),
            'candidate_type_counts': dict(self.candidate.candidate_type_counts),
            'trade_signal_counts': dict(self.candidate.trade_signal_counts),
            'candidate_type_signal_matrix': [row.to_dict() for row in self.matrix_rows],
            'bad_high_risk_audit': [row.to_dict() for row in self.bad_high_risk_rows],
            'warnings': list(self.warnings),
            'decision_recommendation': self.decision_recommendation,
            'generated_at_utc': self.generated_at_utc,
        }


def build_candidate_type_sanity_report(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str | None = None,
    explicit_tickers: Sequence[str] | None = None,
    universe_csv_path: str | Path | None = None,
    price_table: str = 'price_history',
    min_history_rows: int = 252,
    freshness_tolerance_days: int = 0,
) -> CandidateTypeSanityResult:
    candidate = build_candidate_type_diagnostics(
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
    return build_candidate_type_sanity_from_candidate(
        candidate,
        eligibility_rows={row.ticker: row for row in eligibility.results},
        feature_rows={row.ticker: row for row in feature_readiness.rows},
    )


def build_candidate_type_sanity_from_candidate(
    candidate: CandidateTypeDiagnosticsResult,
    *,
    eligibility_rows: Mapping[str, object] | None = None,
    feature_rows: Mapping[str, object] | None = None,
) -> CandidateTypeSanityResult:
    eligibility_rows = eligibility_rows or {}
    feature_rows = feature_rows or {}
    top20_rows = _build_top20_rows(candidate)
    focus_rows = _build_focus_rows(candidate, eligibility_rows=eligibility_rows, feature_rows=feature_rows)
    bad_rows = _build_bad_high_risk_rows(candidate, eligibility_rows=eligibility_rows)
    distribution_rows = _build_distribution_rows(candidate)
    matrix_rows = _build_signal_matrix_rows(candidate)
    warnings = _build_warnings(candidate, bad_rows)
    decision = _recommend_decision(candidate, bad_rows)
    return CandidateTypeSanityResult(
        candidate=candidate,
        top20_rows=top20_rows,
        focus_rows=focus_rows,
        bad_high_risk_rows=bad_rows,
        distribution_rows=distribution_rows,
        matrix_rows=matrix_rows,
        warnings=warnings,
        decision_recommendation=decision,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_candidate_type_sanity_outputs(*, result: CandidateTypeSanityResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / 'top20_candidate_type_audit.csv', [row.to_dict() for row in result.top20_rows])
    _write_csv(out_dir / 'focus_candidate_type_audit.csv', [row.to_dict() for row in result.focus_rows])
    _write_csv(out_dir / 'candidate_type_counts.csv', [row.to_dict() for row in result.distribution_rows])
    _write_csv(out_dir / 'candidate_type_signal_matrix.csv', [row.to_dict() for row in result.matrix_rows])
    (out_dir / 'candidate_type_sanity_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'candidate_type_sanity_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')


def _build_top20_rows(candidate: CandidateTypeDiagnosticsResult) -> tuple[Top20CandidateTypeAuditRow, ...]:
    rows: list[Top20CandidateTypeAuditRow] = []
    for candidate_row, policy_row in zip(candidate.rows[:20], candidate.policy.rows[:20]):
        rows.append(
            Top20CandidateTypeAuditRow(
                ticker=candidate_row.ticker,
                raw_rank=candidate_row.raw_rank,
                raw_score=candidate_row.raw_score,
                trade_signal=candidate_row.trade_signal.value,
                candidate_type=candidate_row.candidate_type.value,
                classification_reasons=candidate_row.classification_reasons,
                classification_warnings=candidate_row.classification_warnings,
                policy_reasons=policy_row.policy_reasons,
                policy_warnings=policy_row.policy_warnings,
                diagnostic_note=_top20_note(candidate_row, policy_row),
            )
        )
    return tuple(rows)


def _build_focus_rows(
    candidate: CandidateTypeDiagnosticsResult,
    *,
    eligibility_rows: Mapping[str, object],
    feature_rows: Mapping[str, object],
) -> tuple[FocusCandidateTypeAuditRow, ...]:
    candidate_rows = {row.ticker: row for row in candidate.rows}
    policy_rows = {row.ticker: row for row in candidate.policy.rows}
    rows: list[FocusCandidateTypeAuditRow] = []
    for ticker in FOCUS_TICKERS:
        eligibility_row = eligibility_rows.get(ticker)
        feature_row = feature_rows.get(ticker)
        candidate_row = candidate_rows.get(ticker)
        policy_row = policy_rows.get(ticker)
        present = eligibility_row is not None or feature_row is not None or candidate_row is not None
        if candidate_row is None or policy_row is None:
            rows.append(
                FocusCandidateTypeAuditRow(
                    ticker=ticker,
                    present_in_universe=present,
                    raw_rank=None,
                    trade_signal=None,
                    candidate_type=None,
                    failed_gates=(),
                    policy_reasons=(),
                    policy_warnings=(),
                    classification_reasons=(),
                    classification_warnings=(),
                    diagnostic_note=_missing_focus_note(eligibility_row, feature_row),
                )
            )
            continue
        failed_gates = tuple(gate for gate in GATE_FIELDS if not getattr(policy_row, gate))
        rows.append(
            FocusCandidateTypeAuditRow(
                ticker=ticker,
                present_in_universe=present,
                raw_rank=candidate_row.raw_rank,
                trade_signal=candidate_row.trade_signal.value,
                candidate_type=candidate_row.candidate_type.value,
                failed_gates=failed_gates,
                policy_reasons=policy_row.policy_reasons,
                policy_warnings=policy_row.policy_warnings,
                classification_reasons=candidate_row.classification_reasons,
                classification_warnings=candidate_row.classification_warnings,
                diagnostic_note=_focus_note(candidate_row, policy_row),
            )
        )
    return tuple(rows)


def _build_bad_high_risk_rows(
    candidate: CandidateTypeDiagnosticsResult,
    *,
    eligibility_rows: Mapping[str, object],
) -> tuple[BadHighRiskAuditRow, ...]:
    candidate_rows = {row.ticker: row for row in candidate.rows}
    results: list[BadHighRiskAuditRow] = []
    for ticker in BAD_HIGH_RISK_TICKERS:
        row = candidate_rows.get(ticker)
        present = ticker in eligibility_rows or row is not None
        if row is None:
            results.append(
                BadHighRiskAuditRow(
                    ticker=ticker,
                    present_in_universe=present,
                    raw_rank=None,
                    trade_signal=None,
                    candidate_type=None,
                    in_top20=False,
                    diagnostic_note='not ranked in current candidate-type output',
                )
            )
            continue
        results.append(
            BadHighRiskAuditRow(
                ticker=ticker,
                present_in_universe=present,
                raw_rank=row.raw_rank,
                trade_signal=row.trade_signal.value,
                candidate_type=row.candidate_type.value,
                in_top20=row.raw_rank <= 20,
                diagnostic_note=_bad_high_risk_note(row),
            )
        )
    return tuple(results)


def _build_distribution_rows(candidate: CandidateTypeDiagnosticsResult) -> tuple[DistributionRow, ...]:
    total = len(candidate.rows)
    rows: list[DistributionRow] = []
    for name, count in sorted(candidate.candidate_type_counts.items()):
        rows.append(DistributionRow('candidate_type', name, count, _pct(count, total)))
    for name, count in sorted(candidate.trade_signal_counts.items()):
        rows.append(DistributionRow('trade_signal', name, count, _pct(count, total)))
    return tuple(rows)


def _build_signal_matrix_rows(candidate: CandidateTypeDiagnosticsResult) -> tuple[SignalMatrixRow, ...]:
    total = len(candidate.rows)
    matrix: Counter[tuple[str, str]] = Counter()
    for row in candidate.rows:
        matrix[(row.candidate_type.value, row.trade_signal.value)] += 1
    rows = [
        SignalMatrixRow(candidate_type=ct, trade_signal=signal, count=count, percent_of_total=_pct(count, total))
        for (ct, signal), count in sorted(matrix.items())
    ]
    return tuple(rows)


def _build_warnings(
    candidate: CandidateTypeDiagnosticsResult,
    bad_rows: Sequence[BadHighRiskAuditRow],
) -> tuple[str, ...]:
    warnings: list[str] = []
    total = len(candidate.rows)
    stable_count = candidate.candidate_type_counts.get(CandidateType.STABLE_LEADER.value, 0)
    early_breakout_count = candidate.candidate_type_counts.get(CandidateType.EARLY_BREAKOUT.value, 0)
    extended_count = candidate.candidate_type_counts.get(CandidateType.EXTENDED_RUNNER.value, 0)
    if stable_count == 0:
        warnings.append('no Stable Leaders exist')
    if total > 0 and stable_count > max(10, int(total * 0.20)):
        warnings.append('too many Stable Leaders exist')
    if early_breakout_count == 0:
        warnings.append('no Early Breakouts exist')
    if extended_count == 0:
        warnings.append('no Extended Runners exist')
    if any(row.trade_signal == TradeSignal.BUY and row.candidate_type not in {CandidateType.STABLE_LEADER, CandidateType.EARLY_BREAKOUT} for row in candidate.rows):
        warnings.append('one or more BUY rows are not Stable Leader or Early Breakout')
    if any(row.trade_signal == TradeSignal.AVOID and row.candidate_type == CandidateType.STABLE_LEADER for row in candidate.rows):
        warnings.append('one or more AVOID rows are Stable Leader')
    if any((row.trade_signal == TradeSignal.BUY.value) or (row.candidate_type == CandidateType.STABLE_LEADER.value) for row in bad_rows if row.present_in_universe):
        warnings.append('one or more bad/high-risk tickers are BUY or Stable Leader')
    if len(candidate.rows) != len(candidate.policy.rows):
        warnings.append('candidate type row count differs from policy row count')
    return tuple(warnings)


def _recommend_decision(candidate: CandidateTypeDiagnosticsResult, bad_rows: Sequence[BadHighRiskAuditRow]) -> str:
    if len(candidate.rows) != len(candidate.policy.rows):
        return DECISION_BLOCKED
    if any(row.trade_signal == TradeSignal.BUY and row.candidate_type not in {CandidateType.STABLE_LEADER, CandidateType.EARLY_BREAKOUT} for row in candidate.rows):
        return DECISION_REVISE_CANDIDATE_TYPES
    if any(row.trade_signal == TradeSignal.AVOID and row.candidate_type == CandidateType.STABLE_LEADER for row in candidate.rows):
        return DECISION_REVISE_CANDIDATE_TYPES
    if any((row.trade_signal == TradeSignal.BUY.value) or (row.candidate_type == CandidateType.STABLE_LEADER.value) for row in bad_rows if row.present_in_universe):
        return DECISION_REVISE_CANDIDATE_TYPES
    if len(candidate.rows) == 0:
        return DECISION_BLOCKED
    return DECISION_CONTINUE


def _top20_note(candidate_row, policy_row) -> str:
    if candidate_row.trade_signal == TradeSignal.BUY:
        return 'clean ranked leader with supportive trade signal'
    if candidate_row.candidate_type == CandidateType.STABLE_LEADER:
        return 'leader profile exists but policy did not clear full BUY gate'
    if candidate_row.candidate_type == CandidateType.EARLY_BREAKOUT:
        return 'improving candidate not yet mature enough for a clean buy'
    if candidate_row.candidate_type == CandidateType.EXTENDED_RUNNER:
        return 'strong trend profile but stretch or risk keeps timing cautious'
    if candidate_row.candidate_type == CandidateType.REBOUND_CASE:
        return f"ranking interest but policy flags rebound-like risk: {', '.join(policy_row.policy_reasons)}"
    return f"rejected by practical quality gates: {', '.join(policy_row.policy_reasons)}"


def _focus_note(candidate_row, policy_row) -> str:
    if candidate_row.trade_signal == TradeSignal.BUY:
        return 'focus ticker clears both policy and candidate-type sanity'
    if candidate_row.candidate_type == CandidateType.EARLY_BREAKOUT:
        return 'focus ticker is improving but remains a watch-style setup'
    if candidate_row.candidate_type == CandidateType.EXTENDED_RUNNER:
        return 'focus ticker trends strongly but looks extended or riskier than entry-quality'
    if candidate_row.candidate_type == CandidateType.REJECT:
        return f"focus ticker is rejected by practical gates: {', '.join(policy_row.policy_reasons)}"
    return f"focus ticker remains non-buy due to {', '.join(policy_row.policy_reasons)}"


def _missing_focus_note(eligibility_row, feature_row) -> str:
    if feature_row is not None and not feature_row.feature_complete:
        return 'feature incomplete'
    if eligibility_row is not None and not eligibility_row.eligible:
        return f"structurally rejected: {', '.join(eligibility_row.rejection_reasons)}"
    if eligibility_row is not None:
        return 'present in universe but not ranked'
    return 'ticker not present in universe'


def _bad_high_risk_note(row) -> str:
    if row.trade_signal == TradeSignal.BUY:
        return 'bad/high-risk ticker should not be BUY'
    if row.candidate_type == CandidateType.STABLE_LEADER:
        return 'bad/high-risk ticker should not be Stable Leader'
    if row.raw_rank <= 20:
        return 'bad/high-risk ticker appears in top 20 but remains downgraded'
    return 'bad/high-risk ticker remains below clean-candidate status'


def _pct(count: int, total: int) -> float:
    if total == 0:
        return 0.0
    return (count / total) * 100.0


def _render_summary_markdown(result: CandidateTypeSanityResult) -> str:
    lines = [
        '# Candidate Type Sanity Summary',
        '',
        '## Run metadata',
        '',
        f'- Universe id: {result.candidate.policy.ranking.universe_id}',
        f'- Universe source: {result.candidate.policy.ranking.universe_source}',
        f'- Benchmark ticker: {result.candidate.policy.ranking.benchmark_ticker or "not requested"}',
        f'- Ranking engine id: {result.candidate.policy.ranking.ranking_engine_id}',
        f'- Policy engine id: {result.candidate.policy.policy_engine_id}',
        f'- Classification engine id: {result.candidate.classification_engine_id}',
        f'- Input universe count: {result.candidate.policy.ranking.input_universe_count}',
        f'- Ranked count: {result.candidate.policy.ranking.ranked_count}',
        f'- Candidate type row count: {len(result.candidate.rows)}',
        f'- Generated at: {result.generated_at_utc}',
        '',
        '## Candidate type distribution',
        '',
    ]
    for row in result.distribution_rows:
        lines.append(f'- {row.distribution} / {row.name}: {row.count} ({row.percent:.1f}%)')
    lines.extend(['', '## Candidate type by signal matrix', ''])
    for row in result.matrix_rows:
        lines.append(f'- {row.candidate_type} x {row.trade_signal}: {row.count} ({row.percent_of_total:.1f}%)')
    lines.extend(['', '## Top 20 audit', ''])
    for row in result.top20_rows:
        lines.append(f'- {row.raw_rank}. {row.ticker}: {row.trade_signal} | {row.candidate_type} | {row.diagnostic_note}')
    lines.extend(['', '## Focus ticker audit', ''])
    for row in result.focus_rows:
        lines.append(f'- {row.ticker}: {row.diagnostic_note}')
    lines.extend(['', '## Bad/high-risk audit', ''])
    for row in result.bad_high_risk_rows:
        lines.append(f'- {row.ticker}: {row.diagnostic_note}')
    lines.extend(['', '## Warnings', ''])
    if result.warnings:
        for warning in result.warnings:
            lines.append(f'- {warning}')
    else:
        lines.append('- none')
    lines.extend(['', '## Decision recommendation', '', f'- {result.decision_recommendation}', ''])
    return '\n'.join(lines)


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = ['ticker']
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

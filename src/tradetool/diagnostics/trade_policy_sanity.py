from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradetool.contracts.enums import TradeSignal
from tradetool.diagnostics.eligibility import build_eligibility_diagnostics
from tradetool.diagnostics.feature_readiness import build_feature_readiness_diagnostics
from tradetool.diagnostics.trade_policy import TradePolicyDiagnosticsResult, build_trade_policy_diagnostics

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
DECISION_CONTINUE = 'continue_to_candidate_types'
DECISION_REVISE = 'revise_trade_policy_thresholds'
DECISION_FIX = 'fix_trade_policy_bug'
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
class FocusPolicyAuditRow:
    ticker: str
    present_in_universe: bool
    raw_rank: int | None
    trade_signal: str | None
    failed_gates: tuple[str, ...]
    warnings: tuple[str, ...]
    diagnostic_note: str

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'present_in_universe': self.present_in_universe,
            'raw_rank': self.raw_rank,
            'trade_signal': self.trade_signal,
            'failed_gates': list(self.failed_gates),
            'warnings': list(self.warnings),
            'diagnostic_note': self.diagnostic_note,
        }


@dataclass(frozen=True, slots=True)
class GateFailureCountRow:
    scope: str
    gate_name: str
    failed_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            'scope': self.scope,
            'gate_name': self.gate_name,
            'failed_count': self.failed_count,
        }


@dataclass(frozen=True, slots=True)
class TradePolicySanityResult:
    policy: TradePolicyDiagnosticsResult
    focus_rows: tuple[FocusPolicyAuditRow, ...]
    gate_failure_counts: tuple[GateFailureCountRow, ...]
    signal_percentages: Mapping[str, float]
    decision_recommendation: str
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'universe_id': self.policy.ranking.universe_id,
            'universe_source': self.policy.ranking.universe_source,
            'benchmark_ticker': self.policy.ranking.benchmark_ticker,
            'ranking_engine_id': self.policy.ranking.ranking_engine_id,
            'policy_engine_id': self.policy.policy_engine_id,
            'input_universe_count': self.policy.ranking.input_universe_count,
            'ranked_count': self.policy.ranking.ranked_count,
            'policy_row_count': len(self.policy.rows),
            'signal_counts': dict(self.policy.signal_counts),
            'signal_percentages': dict(self.signal_percentages),
            'decision_recommendation': self.decision_recommendation,
            'generated_at_utc': self.generated_at_utc,
        }


def build_trade_policy_sanity_report(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str | None = None,
    explicit_tickers: Sequence[str] | None = None,
    universe_csv_path: str | Path | None = None,
    price_table: str = 'price_history',
    min_history_rows: int = 252,
    freshness_tolerance_days: int = 0,
) -> TradePolicySanityResult:
    policy = build_trade_policy_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        benchmark_ticker=benchmark_ticker,
        explicit_tickers=explicit_tickers,
        universe_csv_path=universe_csv_path,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    if _has_inconsistent_policy_rows(policy):
        decision = DECISION_FIX
    else:
        decision = _recommend_decision(policy)
    focus_rows = _build_focus_rows(
        policy=policy,
        db_path=db_path,
        universe_id=universe_id,
        benchmark_ticker=benchmark_ticker,
        explicit_tickers=explicit_tickers,
        universe_csv_path=universe_csv_path,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    gate_failure_counts = _build_gate_failure_counts(policy, focus_rows)
    signal_percentages = _signal_percentages(policy)
    return TradePolicySanityResult(
        policy=policy,
        focus_rows=focus_rows,
        gate_failure_counts=gate_failure_counts,
        signal_percentages=signal_percentages,
        decision_recommendation=decision,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def build_trade_policy_sanity_from_policy(policy: TradePolicyDiagnosticsResult) -> TradePolicySanityResult:
    focus_rows = tuple(
        FocusPolicyAuditRow(
            ticker=ticker,
            present_in_universe=False,
            raw_rank=None,
            trade_signal=None,
            failed_gates=(),
            warnings=(),
            diagnostic_note='ticker not present in ranked policy rows',
        )
        for ticker in FOCUS_TICKERS
    )
    decision = DECISION_FIX if _has_inconsistent_policy_rows(policy) else _recommend_decision(policy)
    return TradePolicySanityResult(
        policy=policy,
        focus_rows=focus_rows,
        gate_failure_counts=_build_gate_failure_counts(policy, focus_rows),
        signal_percentages=_signal_percentages(policy),
        decision_recommendation=decision,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_trade_policy_sanity_outputs(*, result: TradePolicySanityResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / 'top20_policy_audit.csv', [_top20_row_to_dict(row) for row in result.policy.rows[:20]])
    _write_csv(out_dir / 'focus_policy_audit.csv', [row.to_dict() for row in result.focus_rows])
    _write_csv(out_dir / 'gate_failure_counts.csv', [row.to_dict() for row in result.gate_failure_counts])
    (out_dir / 'trade_policy_sanity_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'trade_policy_sanity_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')


def _build_focus_rows(
    *,
    policy: TradePolicyDiagnosticsResult,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str | None,
    explicit_tickers: Sequence[str] | None,
    universe_csv_path: str | Path | None,
    price_table: str,
    min_history_rows: int,
    freshness_tolerance_days: int,
) -> tuple[FocusPolicyAuditRow, ...]:
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
    eligibility_rows = {row.ticker: row for row in eligibility.results}
    feature_rows = {row.ticker: row for row in feature_readiness.rows}
    policy_rows = {row.ticker: row for row in policy.rows}
    rows: list[FocusPolicyAuditRow] = []
    for ticker in FOCUS_TICKERS:
        eligibility_row = eligibility_rows.get(ticker)
        feature_row = feature_rows.get(ticker)
        policy_row = policy_rows.get(ticker)
        present = eligibility_row is not None
        if policy_row is None:
            note = 'ticker not ranked'
            if feature_row is not None and not feature_row.feature_complete:
                note = 'feature incomplete'
            elif eligibility_row is not None and not eligibility_row.eligible:
                note = f"structurally rejected: {', '.join(eligibility_row.rejection_reasons)}"
            rows.append(
                FocusPolicyAuditRow(
                    ticker=ticker,
                    present_in_universe=present,
                    raw_rank=None,
                    trade_signal=None,
                    failed_gates=(),
                    warnings=(),
                    diagnostic_note=note,
                )
            )
            continue
        failed_gates = tuple(gate for gate in GATE_FIELDS if not getattr(policy_row, gate))
        rows.append(
            FocusPolicyAuditRow(
                ticker=ticker,
                present_in_universe=present,
                raw_rank=policy_row.raw_rank,
                trade_signal=policy_row.trade_signal.value,
                failed_gates=failed_gates,
                warnings=policy_row.policy_warnings,
                diagnostic_note=_focus_note(policy_row),
            )
        )
    return tuple(rows)


def _build_gate_failure_counts(
    policy: TradePolicyDiagnosticsResult,
    focus_rows: Sequence[FocusPolicyAuditRow],
) -> tuple[GateFailureCountRow, ...]:
    scopes = {
        'all_ranked_rows': policy.rows,
        'top20_only': policy.rows[:20],
        'avoid_rows_only': tuple(row for row in policy.rows if row.trade_signal == TradeSignal.AVOID),
    }
    focus_policy_rows = {row.ticker for row in focus_rows if row.raw_rank is not None}
    scopes['focus_tickers_only'] = tuple(row for row in policy.rows if row.ticker in focus_policy_rows)
    results: list[GateFailureCountRow] = []
    for scope_name, rows in scopes.items():
        for gate_name in GATE_FIELDS:
            results.append(
                GateFailureCountRow(
                    scope=scope_name,
                    gate_name=gate_name,
                    failed_count=sum(1 for row in rows if not getattr(row, gate_name)),
                )
            )
    return tuple(results)


def _signal_percentages(policy: TradePolicyDiagnosticsResult) -> Mapping[str, float]:
    total = len(policy.rows)
    if total == 0:
        return {}
    return {signal: (count / total) * 100.0 for signal, count in sorted(policy.signal_counts.items())}


def _recommend_decision(policy: TradePolicyDiagnosticsResult) -> str:
    if len(policy.rows) == 0:
        return DECISION_BLOCKED
    buy_count = policy.signal_counts.get(TradeSignal.BUY.value, 0)
    if buy_count == 0:
        return DECISION_REVISE
    top20_avoids = [row for row in policy.rows[:20] if row.trade_signal == TradeSignal.AVOID]
    if top20_avoids:
        reason_counter: Counter[str] = Counter()
        for row in top20_avoids:
            reason_counter.update(row.policy_reasons)
        if reason_counter:
            top_reason, count = reason_counter.most_common(1)[0]
            if count >= max(3, len(top20_avoids) // 2):
                return DECISION_REVISE
    return DECISION_CONTINUE


def _has_inconsistent_policy_rows(policy: TradePolicyDiagnosticsResult) -> bool:
    required_engine_id = policy.policy_engine_id
    for row in policy.rows:
        if row.policy_engine_id != required_engine_id:
            return True
        if row.trade_signal == TradeSignal.BUY and not row.policy_pass:
            return True
        if row.trade_signal != TradeSignal.BUY and row.policy_pass and row.trade_signal != TradeSignal.WATCH:
            return True
        if row.trade_signal != TradeSignal.BUY and not row.policy_reasons:
            return True
    return False


def _focus_note(row) -> str:
    if row.trade_signal == TradeSignal.BUY:
        return 'passes all BUY gates'
    if row.trade_signal == TradeSignal.WATCH:
        return _not_buy_explanation(row)
    if row.trade_signal == TradeSignal.REVIEW:
        return _not_buy_explanation(row)
    return _not_buy_explanation(row)


def _not_buy_explanation(row) -> str:
    reasons = ', '.join(row.policy_reasons)
    warnings = ', '.join(row.policy_warnings)
    if warnings:
        return f'not BUY due to {reasons}; warnings: {warnings}'
    return f'not BUY due to {reasons}'


def _top20_row_to_dict(row) -> dict[str, object]:
    data = row.to_dict()
    data['not_buy_explanation'] = '' if row.trade_signal == TradeSignal.BUY else _not_buy_explanation(row)
    return data


def _render_summary_markdown(result: TradePolicySanityResult) -> str:
    lines = [
        '# Trade Policy Sanity Summary',
        '',
        '## Run metadata',
        '',
        f'- Universe id: {result.policy.ranking.universe_id}',
        f'- Universe source: {result.policy.ranking.universe_source}',
        f'- Benchmark ticker: {result.policy.ranking.benchmark_ticker or "not requested"}',
        f'- Ranking engine id: {result.policy.ranking.ranking_engine_id}',
        f'- Policy engine id: {result.policy.policy_engine_id}',
        f'- Input universe count: {result.policy.ranking.input_universe_count}',
        f'- Ranked count: {result.policy.ranking.ranked_count}',
        f'- Policy row count: {len(result.policy.rows)}',
        f'- Generated at: {result.generated_at_utc}',
        '',
        '## Signal distribution',
        '',
    ]
    for signal, count in sorted(result.policy.signal_counts.items()):
        percentage = result.signal_percentages.get(signal, 0.0)
        lines.append(f'- {signal}: {count} ({percentage:.1f}%)')
    lines.extend(['', '## Top 20 AVOID summary', ''])
    for row in result.policy.rows[:20]:
        if row.trade_signal == TradeSignal.AVOID:
            lines.append(f'- {row.ticker}: {_not_buy_explanation(row)}')
    lines.extend(['', '## Focus ticker audit', ''])
    for row in result.focus_rows:
        lines.append(f"- {row.ticker}: {row.diagnostic_note}")
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

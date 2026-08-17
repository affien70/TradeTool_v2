from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradetool.contracts.enums import TradeSignal
from tradetool.diagnostics.baseline_ranking import BaselineRankingRow
from tradetool.diagnostics.trade_policy import TradePolicyDiagnosticsResult, build_trade_policy_diagnostics
from tradetool.policy.trade_policy import (
    MAX_ACCEPTABLE_VOLATILITY,
    MAX_BUY_DISTANCE_TO_SMA50,
    MAX_BUY_DISTANCE_TO_SMA200,
    MAX_BUY_RAW_RANK,
    MAX_MODERATE_VOLATILITY,
    MAX_WATCH_DISTANCE_TO_SMA50,
    MAX_WATCH_DISTANCE_TO_SMA200,
    MAX_WATCH_RAW_RANK,
    MIN_ACCEPTABLE_DRAWDOWN,
    MIN_ACCEPTABLE_TRADED_VALUE,
    MIN_MODERATE_TRADED_VALUE,
    TRADE_POLICY_ENGINE_ID,
    TradePolicyDiagnosticsRow,
)

POSITIVE_FOCUS_TICKERS = (
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
THRESHOLD_FIELDS = (
    'drawdown_252',
    'volatility_63',
    'average_traded_value_20',
    'distance_to_sma50',
    'distance_to_sma200',
    'return_3m',
    'return_6m',
    'relative_strength_3m',
    'relative_strength_6m',
)
RECOMMEND_APPLY = 'apply_balanced_threshold_revision'
RECOMMEND_KEEP = 'keep_current_policy'
RECOMMEND_REVISE_RANKING = 'revise_baseline_ranking_first'
RECOMMEND_BLOCKED = 'blocked_insufficient_evidence'


@dataclass(frozen=True, slots=True)
class ScenarioThresholds:
    name: str
    min_acceptable_drawdown: float
    max_acceptable_volatility: float
    max_moderate_volatility: float
    min_acceptable_traded_value: float
    min_moderate_traded_value: float
    max_buy_distance_to_sma50: float
    max_watch_distance_to_sma50: float
    max_buy_distance_to_sma200: float
    max_watch_distance_to_sma200: float
    max_buy_raw_rank: int
    max_watch_raw_rank: int


@dataclass(frozen=True, slots=True)
class ThresholdDistributionRow:
    metric_name: str
    scope: str
    min_value: float
    p10: float
    p25: float
    median: float
    p75: float
    p90: float
    max_value: float

    def to_dict(self) -> dict[str, object]:
        return {
            'metric_name': self.metric_name,
            'scope': self.scope,
            'min': self.min_value,
            'p10': self.p10,
            'p25': self.p25,
            'median': self.median,
            'p75': self.p75,
            'p90': self.p90,
            'max': self.max_value,
        }


@dataclass(frozen=True, slots=True)
class ScenarioSignalCountRow:
    scenario_name: str
    trade_signal: str
    count: int

    def to_dict(self) -> dict[str, object]:
        return {
            'scenario_name': self.scenario_name,
            'trade_signal': self.trade_signal,
            'count': self.count,
        }


@dataclass(frozen=True, slots=True)
class ScenarioTickerRow:
    scenario_name: str
    scope: str
    ticker: str
    raw_rank: int | None
    trade_signal: str | None
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            'scenario_name': self.scenario_name,
            'scope': self.scope,
            'ticker': self.ticker,
            'raw_rank': self.raw_rank,
            'trade_signal': self.trade_signal,
            'reasons': list(self.reasons),
            'warnings': list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class ScenarioSummary:
    name: str
    rows: tuple[TradePolicyDiagnosticsRow, ...]
    signal_counts: Mapping[str, int]
    top20: tuple[ScenarioTickerRow, ...]
    focus_rows: tuple[ScenarioTickerRow, ...]
    bad_rows: tuple[ScenarioTickerRow, ...]
    bad_buy_or_watch_count: int
    bad_top20_count: int
    positive_buy_or_watch_count: int
    positive_top20_count: int


@dataclass(frozen=True, slots=True)
class TradePolicyCalibrationResult:
    baseline: TradePolicyDiagnosticsResult
    threshold_distribution: tuple[ThresholdDistributionRow, ...]
    scenario_summaries: tuple[ScenarioSummary, ...]
    recommendation: str
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'universe_id': self.baseline.ranking.universe_id,
            'universe_source': self.baseline.ranking.universe_source,
            'benchmark_ticker': self.baseline.ranking.benchmark_ticker,
            'ranking_engine_id': self.baseline.ranking.ranking_engine_id,
            'policy_engine_id': self.baseline.policy_engine_id,
            'input_universe_count': self.baseline.ranking.input_universe_count,
            'ranked_count': self.baseline.ranking.ranked_count,
            'current_signal_counts': dict(self.baseline.signal_counts),
            'scenario_signal_counts': {
                scenario.name: dict(scenario.signal_counts)
                for scenario in self.scenario_summaries
            },
            'recommendation': self.recommendation,
            'generated_at_utc': self.generated_at_utc,
        }


def build_trade_policy_calibration_report(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str | None = None,
    explicit_tickers: Sequence[str] | None = None,
    universe_csv_path: str | Path | None = None,
    price_table: str = 'price_history',
    min_history_rows: int = 252,
    freshness_tolerance_days: int = 0,
) -> TradePolicyCalibrationResult:
    baseline = build_trade_policy_diagnostics(
        db_path=db_path,
        universe_id=universe_id,
        benchmark_ticker=benchmark_ticker,
        explicit_tickers=explicit_tickers,
        universe_csv_path=universe_csv_path,
        price_table=price_table,
        min_history_rows=min_history_rows,
        freshness_tolerance_days=freshness_tolerance_days,
    )
    distributions = _build_threshold_distribution(baseline.ranking.rows)
    scenarios = tuple(_simulate_scenario(name, thresholds, baseline.rows) for name, thresholds in _scenario_thresholds().items())
    recommendation = _recommend_scenario(baseline, scenarios)
    return TradePolicyCalibrationResult(
        baseline=baseline,
        threshold_distribution=distributions,
        scenario_summaries=scenarios,
        recommendation=recommendation,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_trade_policy_calibration_outputs(*, result: TradePolicyCalibrationResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'trade_policy_calibration_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'trade_policy_calibration_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')
    _write_csv(out_dir / 'threshold_distribution.csv', [row.to_dict() for row in result.threshold_distribution])
    _write_csv(
        out_dir / 'scenario_signal_counts.csv',
        [row.to_dict() for scenario in result.scenario_summaries for row in _scenario_signal_count_rows(scenario)],
    )
    _write_csv(
        out_dir / 'scenario_top20.csv',
        [row.to_dict() for scenario in result.scenario_summaries for row in scenario.top20],
    )
    _write_csv(
        out_dir / 'scenario_focus_tickers.csv',
        [row.to_dict() for scenario in result.scenario_summaries for row in scenario.focus_rows],
    )
    _write_csv(
        out_dir / 'scenario_bad_names.csv',
        [row.to_dict() for scenario in result.scenario_summaries for row in scenario.bad_rows],
    )


def _scenario_thresholds() -> Mapping[str, ScenarioThresholds]:
    return {
        'current': ScenarioThresholds(
            name='current',
            min_acceptable_drawdown=MIN_ACCEPTABLE_DRAWDOWN,
            max_acceptable_volatility=MAX_ACCEPTABLE_VOLATILITY,
            max_moderate_volatility=MAX_MODERATE_VOLATILITY,
            min_acceptable_traded_value=MIN_ACCEPTABLE_TRADED_VALUE,
            min_moderate_traded_value=MIN_MODERATE_TRADED_VALUE,
            max_buy_distance_to_sma50=MAX_BUY_DISTANCE_TO_SMA50,
            max_watch_distance_to_sma50=MAX_WATCH_DISTANCE_TO_SMA50,
            max_buy_distance_to_sma200=MAX_BUY_DISTANCE_TO_SMA200,
            max_watch_distance_to_sma200=MAX_WATCH_DISTANCE_TO_SMA200,
            max_buy_raw_rank=MAX_BUY_RAW_RANK,
            max_watch_raw_rank=MAX_WATCH_RAW_RANK,
        ),
        'drawdown_relaxed': ScenarioThresholds(
            name='drawdown_relaxed',
            min_acceptable_drawdown=-0.40,
            max_acceptable_volatility=MAX_ACCEPTABLE_VOLATILITY,
            max_moderate_volatility=MAX_MODERATE_VOLATILITY,
            min_acceptable_traded_value=MIN_ACCEPTABLE_TRADED_VALUE,
            min_moderate_traded_value=MIN_MODERATE_TRADED_VALUE,
            max_buy_distance_to_sma50=MAX_BUY_DISTANCE_TO_SMA50,
            max_watch_distance_to_sma50=MAX_WATCH_DISTANCE_TO_SMA50,
            max_buy_distance_to_sma200=MAX_BUY_DISTANCE_TO_SMA200,
            max_watch_distance_to_sma200=MAX_WATCH_DISTANCE_TO_SMA200,
            max_buy_raw_rank=MAX_BUY_RAW_RANK,
            max_watch_raw_rank=MAX_WATCH_RAW_RANK,
        ),
        'balanced_relaxed': ScenarioThresholds(
            name='balanced_relaxed',
            min_acceptable_drawdown=-0.40,
            max_acceptable_volatility=0.04,
            max_moderate_volatility=0.06,
            min_acceptable_traded_value=750_000.0,
            min_moderate_traded_value=250_000.0,
            max_buy_distance_to_sma50=0.22,
            max_watch_distance_to_sma50=0.35,
            max_buy_distance_to_sma200=0.45,
            max_watch_distance_to_sma200=0.70,
            max_buy_raw_rank=20,
            max_watch_raw_rank=80,
        ),
        'strict_leader': ScenarioThresholds(
            name='strict_leader',
            min_acceptable_drawdown=-0.20,
            max_acceptable_volatility=0.025,
            max_moderate_volatility=0.04,
            min_acceptable_traded_value=1_500_000.0,
            min_moderate_traded_value=500_000.0,
            max_buy_distance_to_sma50=0.12,
            max_watch_distance_to_sma50=0.25,
            max_buy_distance_to_sma200=0.30,
            max_watch_distance_to_sma200=0.50,
            max_buy_raw_rank=15,
            max_watch_raw_rank=40,
        ),
    }


def _simulate_scenario(
    scenario_name: str,
    thresholds: ScenarioThresholds,
    baseline_rows: Sequence[TradePolicyDiagnosticsRow],
) -> ScenarioSummary:
    rows = tuple(_classify_under_scenario(row, thresholds) for row in baseline_rows)
    signal_counts = dict(sorted(Counter(row.trade_signal.value for row in rows).items()))
    top20 = tuple(_scenario_ticker_row(scenario_name, 'top20', row) for row in rows[:20])
    focus_rows = tuple(_scenario_focus_row(scenario_name, ticker, rows) for ticker in POSITIVE_FOCUS_TICKERS)
    bad_rows = tuple(_scenario_focus_row(scenario_name, ticker, rows) for ticker in BAD_HIGH_RISK_TICKERS)
    buy_watch = {TradeSignal.BUY.value, TradeSignal.WATCH.value}
    bad_buy_or_watch_count = sum(1 for row in bad_rows if row.trade_signal in buy_watch)
    bad_top20_count = sum(1 for row in top20 if row.ticker in BAD_HIGH_RISK_TICKERS)
    positive_buy_or_watch_count = sum(1 for row in focus_rows if row.trade_signal in buy_watch)
    positive_top20_count = sum(1 for row in top20 if row.ticker in POSITIVE_FOCUS_TICKERS)
    return ScenarioSummary(
        name=scenario_name,
        rows=rows,
        signal_counts=signal_counts,
        top20=top20,
        focus_rows=focus_rows,
        bad_rows=bad_rows,
        bad_buy_or_watch_count=bad_buy_or_watch_count,
        bad_top20_count=bad_top20_count,
        positive_buy_or_watch_count=positive_buy_or_watch_count,
        positive_top20_count=positive_top20_count,
    )


def _classify_under_scenario(row: TradePolicyDiagnosticsRow, thresholds: ScenarioThresholds) -> TradePolicyDiagnosticsRow:
    above_sma50 = row.above_sma50
    above_sma200 = row.above_sma200
    positive_return_3m = row.positive_return_3m
    positive_return_6m = row.positive_return_6m
    positive_rs_3m = row.positive_rs_3m
    positive_rs_6m = row.positive_rs_6m
    acceptable_drawdown = row.drawdown_252 >= thresholds.min_acceptable_drawdown
    acceptable_volatility = row.volatility_63 <= thresholds.max_acceptable_volatility
    acceptable_traded_value = row.average_traded_value_20 >= thresholds.min_acceptable_traded_value
    moderate_stretch = (
        abs(row.distance_to_sma50) <= thresholds.max_buy_distance_to_sma50 and
        0.0 <= row.distance_to_sma200 <= thresholds.max_buy_distance_to_sma200
    )
    severe_stretch = (
        abs(row.distance_to_sma50) > thresholds.max_watch_distance_to_sma50 or
        row.distance_to_sma200 > thresholds.max_watch_distance_to_sma200
    )
    reasons: list[str] = []
    warnings: list[str] = []
    if not above_sma200:
        reasons.append('below_sma200')
    if not above_sma50:
        reasons.append('below_sma50')
    if not positive_return_3m:
        reasons.append('non_positive_return_3m')
    if not positive_return_6m:
        reasons.append('non_positive_return_6m')
    if not positive_rs_3m:
        reasons.append('non_positive_relative_strength_3m')
    if not positive_rs_6m:
        reasons.append('non_positive_relative_strength_6m')
    if not acceptable_drawdown:
        reasons.append('deep_drawdown')
    if row.average_traded_value_20 < thresholds.min_moderate_traded_value:
        reasons.append('very_low_traded_value')
    elif not acceptable_traded_value:
        warnings.append('moderate_traded_value')
    if row.volatility_63 > thresholds.max_moderate_volatility:
        reasons.append('high_volatility')
    elif not acceptable_volatility:
        warnings.append('moderate_volatility')
    if severe_stretch:
        reasons.append('extreme_stretch')
    elif not moderate_stretch:
        warnings.append('moderate_stretch')
    if row.raw_rank > thresholds.max_watch_raw_rank:
        warnings.append('lower_ranked_candidate')

    strong_core = all(
        (
            above_sma200,
            above_sma50,
            positive_return_3m,
            positive_return_6m,
            positive_rs_3m,
            positive_rs_6m,
            acceptable_drawdown,
            acceptable_volatility,
            acceptable_traded_value,
            moderate_stretch,
            row.raw_rank <= thresholds.max_buy_raw_rank,
        )
    )
    watch_core = all(
        (
            above_sma200,
            above_sma50,
            positive_rs_3m,
            positive_rs_6m,
            positive_return_3m,
            positive_return_6m,
            row.drawdown_252 >= min(-0.40, thresholds.min_acceptable_drawdown),
            row.volatility_63 <= thresholds.max_moderate_volatility,
            row.average_traded_value_20 >= thresholds.min_moderate_traded_value,
            not severe_stretch,
            row.raw_rank <= thresholds.max_watch_raw_rank,
        )
    )
    severe_failure = any(
        (
            not above_sma200 and not above_sma50,
            row.drawdown_252 < min(-0.40, thresholds.min_acceptable_drawdown),
            row.volatility_63 > max(0.08, thresholds.max_moderate_volatility + 0.02),
            row.average_traded_value_20 < thresholds.min_moderate_traded_value,
            severe_stretch,
            (not positive_rs_3m and not positive_rs_6m),
            (not positive_return_3m and not positive_return_6m),
        )
    )

    if strong_core:
        trade_signal = TradeSignal.BUY
        policy_pass = True
        reasons = ['strong_trend_profile']
    elif watch_core:
        trade_signal = TradeSignal.WATCH
        policy_pass = True
        if not reasons:
            reasons = ['watchlist_candidate']
    elif severe_failure:
        trade_signal = TradeSignal.AVOID
        policy_pass = False
        if not reasons:
            reasons = ['severe_practical_risk']
    else:
        trade_signal = TradeSignal.REVIEW
        policy_pass = False
        if not reasons:
            reasons = ['mixed_practical_profile']

    return TradePolicyDiagnosticsRow(
        ticker=row.ticker,
        rank_date=row.rank_date,
        ranking_engine_id=row.ranking_engine_id,
        policy_engine_id=TRADE_POLICY_ENGINE_ID,
        raw_rank=row.raw_rank,
        raw_score=row.raw_score,
        trade_signal=trade_signal,
        policy_pass=policy_pass,
        policy_reasons=tuple(reasons),
        policy_warnings=tuple(warnings),
        above_sma50=above_sma50,
        above_sma200=above_sma200,
        positive_return_3m=positive_return_3m,
        positive_return_6m=positive_return_6m,
        positive_rs_3m=positive_rs_3m,
        positive_rs_6m=positive_rs_6m,
        acceptable_drawdown=acceptable_drawdown,
        acceptable_volatility=acceptable_volatility,
        acceptable_traded_value=acceptable_traded_value,
        moderate_stretch=moderate_stretch,
        severe_stretch=severe_stretch,
        drawdown_252=row.drawdown_252,
        volatility_63=row.volatility_63,
        average_traded_value_20=row.average_traded_value_20,
        distance_to_sma50=row.distance_to_sma50,
        distance_to_sma200=row.distance_to_sma200,
    )


def _scenario_ticker_row(scenario_name: str, scope: str, row: TradePolicyDiagnosticsRow) -> ScenarioTickerRow:
    return ScenarioTickerRow(
        scenario_name=scenario_name,
        scope=scope,
        ticker=row.ticker,
        raw_rank=row.raw_rank,
        trade_signal=row.trade_signal.value,
        reasons=row.policy_reasons,
        warnings=row.policy_warnings,
    )


def _scenario_focus_row(scenario_name: str, ticker: str, rows: Sequence[TradePolicyDiagnosticsRow]) -> ScenarioTickerRow:
    row = next((item for item in rows if item.ticker == ticker), None)
    if row is None:
        return ScenarioTickerRow(
            scenario_name=scenario_name,
            scope='focus',
            ticker=ticker,
            raw_rank=None,
            trade_signal=None,
            reasons=(),
            warnings=(),
        )
    return _scenario_ticker_row(scenario_name, 'focus', row)


def _build_threshold_distribution(rows: Sequence[BaselineRankingRow]) -> tuple[ThresholdDistributionRow, ...]:
    results: list[ThresholdDistributionRow] = []
    top20 = rows[:20]
    for field in THRESHOLD_FIELDS:
        all_values = [_metric_value(row, field) for row in rows]
        top20_values = [_metric_value(row, field) for row in top20]
        results.append(_distribution_row(field, 'all_ranked_rows', all_values))
        results.append(_distribution_row(field, 'top20', top20_values))
    return tuple(results)


def _distribution_row(metric_name: str, scope: str, values: Sequence[float]) -> ThresholdDistributionRow:
    ordered = sorted(values)
    return ThresholdDistributionRow(
        metric_name=metric_name,
        scope=scope,
        min_value=ordered[0] if ordered else 0.0,
        p10=_percentile(ordered, 0.10),
        p25=_percentile(ordered, 0.25),
        median=_percentile(ordered, 0.50),
        p75=_percentile(ordered, 0.75),
        p90=_percentile(ordered, 0.90),
        max_value=ordered[-1] if ordered else 0.0,
    )


def _metric_value(row: BaselineRankingRow, field: str) -> float:
    value = row.input_fields.get(field)
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def _scenario_signal_count_rows(scenario: ScenarioSummary) -> tuple[ScenarioSignalCountRow, ...]:
    return tuple(
        ScenarioSignalCountRow(scenario_name=scenario.name, trade_signal=signal, count=count)
        for signal, count in sorted(scenario.signal_counts.items())
    )


def _recommend_scenario(baseline: TradePolicyDiagnosticsResult, scenarios: Sequence[ScenarioSummary]) -> str:
    if not scenarios:
        return RECOMMEND_BLOCKED
    if baseline.signal_counts.get(TradeSignal.BUY.value, 0) > 0:
        return RECOMMEND_KEEP
    balanced = next((scenario for scenario in scenarios if scenario.name == 'balanced_relaxed'), None)
    if balanced is not None:
        if (
            balanced.signal_counts.get(TradeSignal.BUY.value, 0) + balanced.signal_counts.get(TradeSignal.WATCH.value, 0) > 0 and
            balanced.bad_buy_or_watch_count == 0 and
            balanced.positive_buy_or_watch_count > _current_positive_buy_or_watch(baseline.rows)
        ):
            return RECOMMEND_APPLY
        if balanced.bad_buy_or_watch_count > 0:
            return RECOMMEND_REVISE_RANKING
    drawdown_heavy_top20 = sum(1 for row in baseline.rows[:20] if 'deep_drawdown' in row.policy_reasons)
    if baseline.signal_counts.get(TradeSignal.BUY.value, 0) == 0 and drawdown_heavy_top20 >= 5:
        return RECOMMEND_APPLY if balanced is not None else RECOMMEND_BLOCKED
    return RECOMMEND_REVISE_RANKING


def _current_positive_buy_or_watch(rows: Sequence[TradePolicyDiagnosticsRow]) -> int:
    return sum(
        1
        for row in rows
        if row.ticker in POSITIVE_FOCUS_TICKERS and row.trade_signal in {TradeSignal.BUY, TradeSignal.WATCH}
    )


def _render_summary_markdown(result: TradePolicyCalibrationResult) -> str:
    lines = [
        '# Trade Policy Calibration Summary',
        '',
        f'- Universe: {result.baseline.ranking.universe_id}',
        f'- Universe source: {result.baseline.ranking.universe_source}',
        f'- Benchmark: {result.baseline.ranking.benchmark_ticker or "not requested"}',
        f'- Ranking engine id: {result.baseline.ranking.ranking_engine_id}',
        f'- Policy engine id: {result.baseline.policy_engine_id}',
        f'- Input universe count: {result.baseline.ranking.input_universe_count}',
        f'- Ranked count: {result.baseline.ranking.ranked_count}',
        f'- Generated at: {result.generated_at_utc}',
        '',
        '## Current policy baseline',
        '',
    ]
    for signal, count in sorted(result.baseline.signal_counts.items()):
        lines.append(f'- {signal}: {count}')
    lines.extend(['', '## Scenario summary', ''])
    for scenario in result.scenario_summaries:
        counts = ', '.join(f'{signal}={count}' for signal, count in sorted(scenario.signal_counts.items()))
        lines.append(f'- {scenario.name}: {counts}')
    lines.extend(['', '## Recommendation', '', f'- {result.recommendation}', ''])
    return '\n'.join(lines)


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

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from tradetool.data import ReadOnlySQLite
from tradetool.diagnostics.market_data_write_test import (
    MarketDataWriteTestResult,
    build_market_data_write_test_result,
)
from tradetool.ui.screener import (
    PRICE_TABLE_V2,
    MinimalScreenerResult,
    MinimalScreenerTableRow,
    SelectedTickerChartDetail,
    build_minimal_screener_result,
    build_selected_ticker_chart_detail,
)
from tradetool.universe.tickers import load_universe_tickers

DEFAULT_REFERENCE_DB_PATH = Path('/Users/affien/DEV/TradeTool/portfolio.sqlite')
DEFAULT_EVIDENCE_DIR = Path('evidence/v1_baseline/20260622T200335Z')
DEFAULT_FOCUS_TICKERS = (
    'DNB.OL',
    'NONG.OL',
    'BONHR.OL',
    'SNTIA.OL',
    'ELK.OL',
    'BEWI.OL',
    'STB.OL',
    'MEDI.OL',
    'VEI.OL',
    'SB1NO.OL',
    'HAFNI.OL',
    'KCC.OL',
    'OMDA.OL',
    'NRC.OL',
    'JIN.OL',
    'EIOF.OL',
    'ELMRA.OL',
    'CAMBI.OL',
    'AKRBP.OL',
    'AFK.OL',
    'MPCC.OL',
    'SUBC.OL',
    'ENDUR.OL',
    'FRO.OL',
    'KIT.OL',
    'HAUTO.OL',
    'BWLPG.OL',
    'EQNR.OL',
    'WWI.OL',
)
DEFAULT_PRIOR_BAD_TICKERS = ('NBX.OL', 'PRS.OL', 'ZENA.OL', 'BCS.OL', 'MORLD.OL')
DECISION_CONTINUE = 'continue_to_holdout_backtest_comparison'
DECISION_BUY_WATCH = 'investigate_buy_watch_quality'
DECISION_FOCUS = 'investigate_rejected_focus_tickers'
DECISION_POLICY = 'adjust_policy_before_backtest'
DECISION_DATA = 'investigate_data_quality_before_quality_review'
DECISION_PROVIDER = 'blocked_provider_failure'
EXPECTED_REPORT_FILES = (
    'baseline_overlap.csv',
    'buy_watch_review_quality.csv',
    'candidate_quality_decision.csv',
    'candidate_quality_summary.json',
    'candidate_quality_summary.md',
    'current_top_candidates.csv',
    'focus_ticker_quality.csv',
    'red_flag_audit.csv',
)
SERIOUS_RED_FLAGS = {
    'stale_or_missing_latest_data',
    'invalid_or_skipped_ohlc_involvement',
    'negative_3m_return',
    'negative_6m_return',
    'negative_3m_relative_strength',
    'negative_6m_relative_strength',
    'below_sma50',
    'below_sma200',
    'deep_drawdown',
    'high_volatility',
    'very_low_liquidity',
    'extreme_stretch',
}
WARNING_RED_FLAGS = {'moderate_liquidity', 'moderate_volatility', 'moderate_stretch'}


@dataclass(frozen=True, slots=True)
class FocusTickerQualityRow:
    ticker: str
    status: str
    enough_history: bool
    mechanically_justified: bool
    row: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        payload = dict(self.row)
        payload.update(
            {
                'ticker': self.ticker,
                'status': self.status,
                'enough_history': self.enough_history,
                'mechanically_justified': self.mechanically_justified,
            }
        )
        return payload


@dataclass(frozen=True, slots=True)
class CandidateQualityComparisonResult:
    universe_id: str
    universe_source: str
    benchmark_ticker: str
    data_source: str
    start_date: str
    stock_ticker_count: int
    seed_result: MarketDataWriteTestResult
    screener_result: MinimalScreenerResult
    current_top_candidates: tuple[dict[str, object], ...]
    buy_watch_review_rows: tuple[dict[str, object], ...]
    focus_rows: tuple[FocusTickerQualityRow, ...]
    baseline_overlap_rows: tuple[dict[str, object], ...]
    red_flag_rows: tuple[dict[str, object], ...]
    prior_bad_rows: tuple[dict[str, object], ...]
    missing_evidence_files: tuple[str, ...]
    decision_recommendation: str
    decision_reasons: tuple[str, ...]
    chart_detail_status: str
    chart_detail_warning: str | None
    chart_detail_point_count: int
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        seed_summary = self.seed_result.to_summary_dict()
        serious_buy_watch = [
            row for row in self.red_flag_rows
            if row.get('trade_signal') in {'BUY', 'WATCH'} and row.get('severity') == 'serious'
        ]
        warning_buy_watch = [
            row for row in self.red_flag_rows
            if row.get('trade_signal') in {'BUY', 'WATCH'} and row.get('severity') == 'warning'
        ]
        overlap_by_method = _baseline_overlap_by_method(self.baseline_overlap_rows)
        return {
            'universe_id': self.universe_id,
            'universe_source': self.universe_source,
            'benchmark_ticker': self.benchmark_ticker,
            'data_source': self.data_source,
            'start_date': self.start_date,
            'stock_ticker_count': self.stock_ticker_count,
            'seed': seed_summary,
            'screener': {
                'price_table': self.screener_result.price_table,
                'ranked_count': self.screener_result.ranked_count,
                'feature_complete_count': self.screener_result.feature_complete_count,
                'feature_incomplete_count': self.screener_result.feature_incomplete_count,
                'close_input_source': self.screener_result.close_input_source,
                'benchmark_alignment_date': self.screener_result.benchmark_alignment_date,
                'benchmark_lag_warning_count': self.screener_result.benchmark_lag_warning_count,
                'trade_signal_counts': dict(self.screener_result.trade_signal_counts),
                'candidate_type_counts': dict(self.screener_result.candidate_type_counts),
            },
            'quality': {
                'buy_watch_count': sum(1 for row in self.screener_result.rows if row.trade_signal in {'BUY', 'WATCH'}),
                'buy_count': self.screener_result.trade_signal_counts.get('BUY', 0),
                'watch_count': self.screener_result.trade_signal_counts.get('WATCH', 0),
                'serious_buy_watch_red_flag_count': len(serious_buy_watch),
                'warning_buy_watch_red_flag_count': len(warning_buy_watch),
                'prior_bad_buy_count': sum(1 for row in self.prior_bad_rows if row.get('trade_signal') == 'BUY'),
                'unexplained_rejected_focus_count': sum(
                    1 for row in self.focus_rows
                    if row.status == 'present_ranked'
                    and row.row.get('trade_signal') in {'REVIEW', 'AVOID'}
                    and not (row.row.get('policy_reasons') or row.row.get('classification_reasons'))
                ),
            },
            'baseline': {
                'missing_evidence_files': list(self.missing_evidence_files),
                'overlap_row_count': len(self.baseline_overlap_rows),
                'current_top20_overlap_by_method': overlap_by_method['top20'],
                'current_top30_overlap_by_method': overlap_by_method['top30'],
            },
            'prior_bad_high_risk_rows': list(self.prior_bad_rows),
            'focus_ticker_rows': [row.to_dict() for row in self.focus_rows],
            'buy_watch_red_flags': list(self.red_flag_rows),
            'chart_detail': {
                'status': self.chart_detail_status,
                'warning': self.chart_detail_warning,
                'point_count': self.chart_detail_point_count,
            },
            'decision_recommendation': self.decision_recommendation,
            'decision_reasons': list(self.decision_reasons),
            'generated_at_utc': self.generated_at_utc,
        }


def build_candidate_quality_comparison(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str,
    start_date: date,
    source_name: str,
    allow_test_db_write: bool,
    allow_partial_invalid_skip: bool,
    universe_db_path: str | Path = DEFAULT_REFERENCE_DB_PATH,
    evidence_dir: str | Path = DEFAULT_EVIDENCE_DIR,
    end_date: date | None = None,
    source_override=None,
    seed_builder: Callable[..., MarketDataWriteTestResult] = build_market_data_write_test_result,
    screener_builder: Callable[..., MinimalScreenerResult] = build_minimal_screener_result,
    chart_builder: Callable[..., SelectedTickerChartDetail] = build_selected_ticker_chart_detail,
    focus_tickers: Sequence[str] = DEFAULT_FOCUS_TICKERS,
    prior_bad_tickers: Sequence[str] = DEFAULT_PRIOR_BAD_TICKERS,
) -> CandidateQualityComparisonResult:
    if not allow_test_db_write:
        raise ValueError('Explicit --allow-test-db-write approval is required for temporary market-data writes.')
    stock_tickers, universe_source = load_norway_v2_stock_tickers(
        universe_id=universe_id,
        universe_db_path=universe_db_path,
        benchmark_ticker=benchmark_ticker,
    )
    requested_tickers = [*stock_tickers, benchmark_ticker.strip().upper()]
    seed_kwargs = {
        'db_path': db_path,
        'tickers': requested_tickers,
        'start_date': start_date,
        'end_date': date.today() if end_date is None else end_date,
        'source_name': source_name,
        'allow_test_db_write': allow_test_db_write,
        'allow_partial_invalid_skip': allow_partial_invalid_skip,
    }
    if source_override is not None:
        seed_kwargs['source_override'] = source_override
    seed_result = seed_builder(**seed_kwargs)
    screener_result = screener_builder(
        db_path=db_path,
        universe_id=universe_id,
        benchmark_ticker=benchmark_ticker,
        explicit_tickers=stock_tickers,
        price_table=PRICE_TABLE_V2,
        data_source=source_name,
    )
    current_rows = tuple(_row_with_quality(row, seed_result=seed_result) for row in screener_result.rows)
    current_top_candidates = current_rows[:30]
    buy_watch_review_rows = tuple(row for row in current_rows if row['trade_signal'] in {'BUY', 'WATCH', 'REVIEW'})
    focus_rows = build_focus_ticker_rows(
        focus_tickers=focus_tickers,
        current_rows=current_rows,
        requested_tickers=stock_tickers,
        feature_incomplete_tickers=_feature_incomplete_tickers(screener_result),
    )
    evidence_path = Path(evidence_dir)
    baseline_rows, missing_evidence_files = build_baseline_overlap_rows(
        current_rows=current_rows,
        evidence_dir=evidence_path,
    )
    red_flag_rows = build_red_flag_rows(current_rows=current_rows)
    prior_bad_rows = tuple(_prior_bad_row(ticker, current_rows=current_rows) for ticker in _normalize_tickers(prior_bad_tickers))
    chart_status, chart_warning, chart_points = _build_chart_status(
        db_path=db_path,
        benchmark_ticker=benchmark_ticker,
        source_name=source_name,
        current_rows=current_rows,
        chart_builder=chart_builder,
    )
    decision, decision_reasons = recommend_next_action(
        seed_result=seed_result,
        screener_result=screener_result,
        red_flag_rows=red_flag_rows,
        focus_rows=focus_rows,
        prior_bad_rows=prior_bad_rows,
    )
    return CandidateQualityComparisonResult(
        universe_id=universe_id,
        universe_source=universe_source,
        benchmark_ticker=benchmark_ticker.strip().upper(),
        data_source=source_name,
        start_date=start_date.isoformat(),
        stock_ticker_count=len(stock_tickers),
        seed_result=seed_result,
        screener_result=screener_result,
        current_top_candidates=current_top_candidates,
        buy_watch_review_rows=buy_watch_review_rows,
        focus_rows=focus_rows,
        baseline_overlap_rows=baseline_rows,
        red_flag_rows=red_flag_rows,
        prior_bad_rows=prior_bad_rows,
        missing_evidence_files=missing_evidence_files,
        decision_recommendation=decision,
        decision_reasons=decision_reasons,
        chart_detail_status=chart_status,
        chart_detail_warning=chart_warning,
        chart_detail_point_count=chart_points,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def load_norway_v2_stock_tickers(
    *,
    universe_id: str,
    universe_db_path: str | Path,
    benchmark_ticker: str,
) -> tuple[tuple[str, ...], str]:
    database = ReadOnlySQLite(universe_db_path)
    selection = load_universe_tickers(universe_id=universe_id, database=database)
    benchmark = benchmark_ticker.strip().upper()
    stock_tickers = tuple(
        ticker for ticker in selection.tickers
        if ticker.endswith('.OL') and not ticker.startswith('^') and ticker != benchmark
    )
    if not stock_tickers:
        raise ValueError(f'Universe "{universe_id}" did not produce any .OL stock tickers.')
    return stock_tickers, selection.source


def write_candidate_quality_outputs(*, result: CandidateQualityComparisonResult, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'candidate_quality_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'candidate_quality_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')
    _write_csv(path / 'current_top_candidates.csv', result.current_top_candidates)
    _write_csv(path / 'buy_watch_review_quality.csv', result.buy_watch_review_rows)
    _write_csv(path / 'focus_ticker_quality.csv', [row.to_dict() for row in result.focus_rows])
    _write_csv(path / 'baseline_overlap.csv', result.baseline_overlap_rows)
    _write_csv(path / 'red_flag_audit.csv', result.red_flag_rows)
    _write_csv(
        path / 'candidate_quality_decision.csv',
        [
            {
                'decision_recommendation': result.decision_recommendation,
                'decision_reasons': '; '.join(result.decision_reasons),
            }
        ],
    )


def build_red_flags(row: Mapping[str, object]) -> tuple[str, ...]:
    flags: list[str] = []
    if _is_blank(row.get('latest_price_date')):
        flags.append('stale_or_missing_latest_data')
    if bool(row.get('invalid_or_skipped_ohlc_involvement')):
        flags.append('invalid_or_skipped_ohlc_involvement')
    if _as_float(row.get('return_3m')) <= 0.0:
        flags.append('negative_3m_return')
    if _as_float(row.get('return_6m')) <= 0.0:
        flags.append('negative_6m_return')
    if _as_float(row.get('relative_strength_3m')) <= 0.0:
        flags.append('negative_3m_relative_strength')
    if _as_float(row.get('relative_strength_6m')) <= 0.0:
        flags.append('negative_6m_relative_strength')
    if row.get('above_sma50') is False:
        flags.append('below_sma50')
    if row.get('above_sma200') is False:
        flags.append('below_sma200')
    if _as_float(row.get('drawdown_252')) < -0.40:
        flags.append('deep_drawdown')
    if _as_float(row.get('volatility_63')) > 0.06:
        flags.append('high_volatility')
    traded_value = _as_float(row.get('average_traded_value_20'))
    if traded_value < 250_000.0:
        flags.append('very_low_liquidity')
    elif traded_value < 750_000.0:
        flags.append('moderate_liquidity')
    if _as_float(row.get('volatility_63')) > 0.04 and _as_float(row.get('volatility_63')) <= 0.06:
        flags.append('moderate_volatility')
    if bool(row.get('severe_stretch')) or 'extreme_stretch' in str(row.get('policy_reasons', '')):
        flags.append('extreme_stretch')
    elif 'moderate_stretch' in str(row.get('policy_warnings', '')):
        flags.append('moderate_stretch')
    return tuple(dict.fromkeys(flags))


def red_flag_severity(flag: str) -> str:
    if flag in SERIOUS_RED_FLAGS:
        return 'serious'
    if flag in WARNING_RED_FLAGS:
        return 'warning'
    return 'info'


def build_red_flag_rows(*, current_rows: Sequence[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for row in current_rows:
        if row.get('trade_signal') not in {'BUY', 'WATCH'}:
            continue
        for flag in build_red_flags(row):
            rows.append(
                {
                    'ticker': row.get('ticker'),
                    'raw_rank': row.get('raw_rank'),
                    'trade_signal': row.get('trade_signal'),
                    'candidate_type': row.get('candidate_type'),
                    'red_flag': flag,
                    'severity': red_flag_severity(flag),
                }
            )
    return tuple(rows)


def build_focus_ticker_rows(
    *,
    focus_tickers: Sequence[str],
    current_rows: Sequence[Mapping[str, object]],
    requested_tickers: Sequence[str],
    feature_incomplete_tickers: Sequence[str],
) -> tuple[FocusTickerQualityRow, ...]:
    rows_by_ticker = {str(row['ticker']): row for row in current_rows}
    requested = set(_normalize_tickers(requested_tickers))
    incomplete = set(_normalize_tickers(feature_incomplete_tickers))
    output: list[FocusTickerQualityRow] = []
    for ticker in _normalize_tickers(focus_tickers):
        row = rows_by_ticker.get(ticker)
        if row is not None:
            output.append(
                FocusTickerQualityRow(
                    ticker=ticker,
                    status='present_ranked',
                    enough_history=True,
                    mechanically_justified=_is_mechanically_justified(row),
                    row=row,
                )
            )
        else:
            status = 'present_not_enough_history' if ticker in requested or ticker in incomplete else 'missing_from_universe_or_provider'
            output.append(FocusTickerQualityRow(ticker=ticker, status=status, enough_history=False, mechanically_justified=False, row={}))
    return tuple(output)


def build_baseline_overlap_rows(
    *,
    current_rows: Sequence[Mapping[str, object]],
    evidence_dir: Path,
) -> tuple[tuple[dict[str, object], ...], tuple[str, ...]]:
    comparison_path = evidence_dir / 'ose_method_comparison_top20.csv'
    focus_path = evidence_dir / 'focus_ticker_baseline.csv'
    missing = []
    if not comparison_path.exists():
        missing.append(str(comparison_path))
        if not focus_path.exists():
            missing.append(str(focus_path))
        return (), tuple(missing)
    if not focus_path.exists():
        missing.append(str(focus_path))

    current_by_ticker = {str(row['ticker']): row for row in current_rows}
    current_top20 = {str(row['ticker']) for row in current_rows[:20]}
    current_top30 = {str(row['ticker']) for row in current_rows[:30]}
    output: list[dict[str, object]] = []
    with comparison_path.open('r', encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        for evidence_row in reader:
            ticker = _normalize_ticker(evidence_row.get('ticker'))
            if ticker is None:
                continue
            current = current_by_ticker.get(ticker, {})
            output.append(
                {
                    'baseline_method': evidence_row.get('method', ''),
                    'baseline_ticker': ticker,
                    'baseline_rank': evidence_row.get('method_rank', ''),
                    'in_current_top20': ticker in current_top20,
                    'in_current_top30': ticker in current_top30,
                    'current_raw_rank': current.get('raw_rank', ''),
                    'current_raw_score': current.get('raw_score', ''),
                    'current_trade_signal': current.get('trade_signal', ''),
                    'current_candidate_type': current.get('candidate_type', ''),
                }
            )
    return tuple(output), tuple(missing)


def _baseline_overlap_by_method(rows: Sequence[Mapping[str, object]]) -> dict[str, dict[str, int]]:
    top20: Counter[str] = Counter()
    top30: Counter[str] = Counter()
    for row in rows:
        method = str(row.get('baseline_method', ''))
        if row.get('in_current_top20'):
            top20[method] += 1
        if row.get('in_current_top30'):
            top30[method] += 1
    return {'top20': dict(sorted(top20.items())), 'top30': dict(sorted(top30.items()))}


def recommend_next_action(
    *,
    seed_result: MarketDataWriteTestResult,
    screener_result: MinimalScreenerResult,
    red_flag_rows: Sequence[Mapping[str, object]],
    focus_rows: Sequence[FocusTickerQualityRow],
    prior_bad_rows: Sequence[Mapping[str, object]],
) -> tuple[str, tuple[str, ...]]:
    if seed_result.provider_error or not seed_result.fetched_tickers:
        return DECISION_PROVIDER, ('provider_failure_prevented_useful_validation',)
    if screener_result.ranked_count == 0 or screener_result.ranked_count < max(1, screener_result.input_universe_count // 2):
        return DECISION_DATA, ('insufficient_ranked_coverage_for_candidate_quality_review',)
    serious_buy_watch = [
        row for row in red_flag_rows
        if row.get('trade_signal') in {'BUY', 'WATCH'} and row.get('severity') == 'serious'
    ]
    if serious_buy_watch:
        return DECISION_BUY_WATCH, ('buy_watch_candidates_have_serious_mechanical_red_flags',)
    prior_bad_buy = [row for row in prior_bad_rows if row.get('trade_signal') == 'BUY']
    if prior_bad_buy:
        return DECISION_POLICY, ('prior_bad_or_high_risk_raw_ml_names_are_still_buy',)
    unexplained_rejects = [
        row for row in focus_rows
        if row.status == 'present_ranked'
        and row.row.get('trade_signal') in {'REVIEW', 'AVOID'}
        and not (row.row.get('policy_reasons') or row.row.get('classification_reasons'))
    ]
    if unexplained_rejects:
        return DECISION_FOCUS, ('important_focus_tickers_rejected_without_clear_reasons',)
    return DECISION_CONTINUE, ('buy_watch_clean_prior_bad_blocked_rejected_focus_explained',)


def remove_temp_db_if_allowed(db_path: str | Path) -> bool:
    path = Path(db_path).expanduser().resolve()
    if path.exists() and path.is_file() and path.is_relative_to(Path('/tmp').resolve()):
        path.unlink()
        return True
    return False


def _row_with_quality(row: MinimalScreenerTableRow, *, seed_result: MarketDataWriteTestResult) -> dict[str, object]:
    payload = row.to_dict()
    skipped_tickers = {invalid.ticker for invalid in seed_result.invalid_rows}
    payload['invalid_or_skipped_ohlc_involvement'] = row.ticker in skipped_tickers
    flags = build_red_flags(payload)
    payload['red_flags'] = '; '.join(flags)
    payload['serious_red_flags'] = '; '.join(flag for flag in flags if red_flag_severity(flag) == 'serious')
    payload['warning_red_flags'] = '; '.join(flag for flag in flags if red_flag_severity(flag) == 'warning')
    payload['mechanically_justified'] = _is_mechanically_justified(payload)
    return payload


def _prior_bad_row(ticker: str, *, current_rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    current = next((row for row in current_rows if row.get('ticker') == ticker), None)
    if current is None:
        return {'ticker': ticker, 'status': 'not_ranked', 'trade_signal': '', 'candidate_type': '', 'raw_rank': ''}
    return {
        'ticker': ticker,
        'status': 'present_ranked',
        'raw_rank': current.get('raw_rank'),
        'raw_score': current.get('raw_score'),
        'trade_signal': current.get('trade_signal'),
        'candidate_type': current.get('candidate_type'),
        'policy_reasons': current.get('policy_reasons'),
        'red_flags': current.get('red_flags'),
        'blocked_from_buy': current.get('trade_signal') != 'BUY',
    }


def _feature_incomplete_tickers(result: MinimalScreenerResult) -> tuple[str, ...]:
    rows = getattr(result, 'rows', ())
    ranked = {row.ticker for row in rows}
    return tuple(ticker for ticker in getattr(result, 'requested_tickers', ()) if ticker not in ranked)


def _build_chart_status(
    *,
    db_path: str | Path,
    benchmark_ticker: str,
    source_name: str,
    current_rows: Sequence[Mapping[str, object]],
    chart_builder: Callable[..., SelectedTickerChartDetail],
) -> tuple[str, str | None, int]:
    if not current_rows:
        return 'not_run_no_ranked_rows', None, 0
    top_ticker = str(current_rows[0]['ticker'])
    try:
        chart = chart_builder(
            db_path=db_path,
            ticker=top_ticker,
            benchmark_ticker=benchmark_ticker,
            price_table=PRICE_TABLE_V2,
            data_source=source_name,
        )
    except Exception as exc:
        return 'failed', str(exc), 0
    return 'completed', chart.warning, len(chart.price_points)


def _is_mechanically_justified(row: Mapping[str, object]) -> bool:
    signal = row.get('trade_signal')
    serious = [flag for flag in build_red_flags(row) if red_flag_severity(flag) == 'serious']
    if signal in {'BUY', 'WATCH'}:
        return not serious
    return bool(row.get('policy_reasons') or row.get('classification_reasons') or serious)


def _render_summary_markdown(result: CandidateQualityComparisonResult) -> str:
    summary = result.to_summary_dict()
    seed = summary['seed']
    screener = summary['screener']
    quality = summary['quality']
    prior_bad_buy = [row for row in result.prior_bad_rows if row.get('trade_signal') == 'BUY']
    overlap_by_method = summary['baseline']['current_top20_overlap_by_method']
    prior_bad_summary = {
        str(row['ticker']): row.get('trade_signal') or row.get('status')
        for row in result.prior_bad_rows
    }
    lines = [
        '# Candidate Quality Comparison',
        '',
        '## Executive Summary',
        f"- Decision recommendation: `{result.decision_recommendation}`",
        f"- Universe source: `{result.universe_source}`",
        f"- Stock tickers: {result.stock_ticker_count}",
        f"- Seed fetched tickers: {len(result.seed_result.fetched_tickers)}",
        f"- Missing tickers: {len(result.seed_result.missing_tickers)}",
        f"- Inserted temp rows: {seed['inserted_count']}",
        f"- Skipped invalid rows: {seed['skipped_invalid_row_count']}",
        f"- Ranked candidates: {screener['ranked_count']}",
        f"- BUY/WATCH rows: {quality['buy_watch_count']}",
        f"- Serious BUY/WATCH red flags: {quality['serious_buy_watch_red_flag_count']}",
        f"- Prior bad/high-risk BUY rows: {len(prior_bad_buy)}",
        '',
        '## Screener Result',
        f"- Price table: `{screener['price_table']}`",
        f"- Close input source: `{screener['close_input_source']}`",
        f"- Benchmark: `{result.benchmark_ticker}`",
        f"- Benchmark alignment date: `{screener['benchmark_alignment_date']}`",
        f"- Benchmark lag warnings: {screener['benchmark_lag_warning_count']}",
        f"- Signal counts: `{screener['trade_signal_counts']}`",
        f"- Candidate type counts: `{screener['candidate_type_counts']}`",
        '',
        '## Baseline Evidence',
        f"- Missing evidence files: `{list(result.missing_evidence_files)}`",
        f"- Current top-20 overlap by method: `{overlap_by_method}`",
        f"- Prior bad/high-risk names: `{prior_bad_summary}`",
        '',
        '## Quality Checks',
        f"- BUY rows mechanically clean: `{quality['serious_buy_watch_red_flag_count'] == 0}`",
        f"- WATCH warnings visible: `{quality['warning_buy_watch_red_flag_count'] > 0}`",
        f"- Prior bad/high-risk names blocked from BUY: `{len(prior_bad_buy) == 0}`",
        "- Rejected focus tickers explained: "
        f"`{quality['unexplained_rejected_focus_count'] == 0}`",
        "- Sector concentration assessed: `not_available_no_sector_metadata`",
        f"- Chart/detail helper status: `{result.chart_detail_status}`",
        '',
        '## Decision Reasons',
        *[f'- `{reason}`' for reason in result.decision_reasons],
    ]
    return '\n'.join(lines) + '\n'


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = _fieldnames(rows)
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key, '')) for key in fieldnames})


def _fieldnames(rows: Sequence[Mapping[str, object]]) -> list[str]:
    names: list[str] = []
    for row in rows:
        for key in row:
            if key not in names:
                names.append(str(key))
    return names or ['empty']


def _csv_value(value: object) -> object:
    if isinstance(value, (tuple, list)):
        return '; '.join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return '' if value is None else value


def _normalize_tickers(tickers: Iterable[str]) -> tuple[str, ...]:
    output: list[str] = []
    for ticker in tickers:
        normalized = _normalize_ticker(ticker)
        if normalized is not None and normalized not in output:
            output.append(normalized)
    return tuple(output)


def _normalize_ticker(ticker: object) -> str | None:
    if ticker is None:
        return None
    normalized = str(ticker).strip().upper()
    return normalized or None


def _as_float(value: object) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def _is_blank(value: object) -> bool:
    return value is None or str(value).strip() == ''

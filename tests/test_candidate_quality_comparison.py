from __future__ import annotations

import json
import sqlite3
import unittest
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from unittest.mock import patch

from tradetool.contracts.enums import CandidateType, TradeSignal
from tradetool.data.market_data_schema import MarketDataDryRunValidationRow, MarketDataWriteResult
from tradetool.diagnostics.candidate_quality_comparison import (
    DECISION_BUY_WATCH,
    DECISION_CONTINUE,
    EXPECTED_REPORT_FILES,
    build_baseline_overlap_rows,
    build_candidate_quality_comparison,
    build_focus_ticker_rows,
    build_red_flags,
    recommend_next_action,
    remove_temp_db_if_allowed,
    write_candidate_quality_outputs,
)
from tradetool.diagnostics.candidate_quality_comparison_cli import build_argument_parser
from tradetool.diagnostics.candidate_quality_comparison_cli import main as candidate_quality_cli_main
from tradetool.diagnostics.market_data_write_test import MarketDataWriteTestResult
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import (
    MAX_BUY_RAW_RANK,
    MIN_ACCEPTABLE_TRADED_VALUE,
    TRADE_POLICY_ENGINE_ID,
)
from tradetool.ranking.baseline import BASELINE_RANKING_ENGINE_ID
from tradetool.ui.screener import CandidateSignalMatrixRow, MinimalScreenerResult, MinimalScreenerTableRow


@dataclass(frozen=True, slots=True)
class _Chart:
    warning: str | None
    price_points: tuple[object, ...]


def _row(
    *,
    ticker: str,
    raw_rank: int,
    raw_score: float,
    trade_signal: str,
    candidate_type: str,
    return_3m: float = 0.08,
    return_6m: float = 0.14,
    rs3m: float = 0.03,
    rs6m: float = 0.04,
    above_sma50: bool = True,
    above_sma200: bool = True,
    drawdown: float = -0.20,
    volatility: float = 0.02,
    traded_value: float = 2_000_000.0,
    warnings: tuple[str, ...] = (),
) -> MinimalScreenerTableRow:
    return MinimalScreenerTableRow(
        raw_rank=raw_rank,
        ticker=ticker,
        raw_score=raw_score,
        trade_signal=trade_signal,
        candidate_type=candidate_type,
        latest_close=100.0,
        latest_price_date='2026-09-09',
        return_1m=0.02,
        return_3m=return_3m,
        return_6m=return_6m,
        return_12m=0.18,
        relative_strength_1m=0.01,
        relative_strength_3m=rs3m,
        relative_strength_6m=rs6m,
        relative_strength_12m=0.05,
        above_sma50=above_sma50,
        above_sma100=True,
        above_sma200=above_sma200,
        drawdown_252=drawdown,
        volatility_63=volatility,
        average_traded_value_20=traded_value,
        distance_to_sma50=0.05,
        distance_to_sma200=0.20,
        policy_reasons=('strong_trend_profile',) if trade_signal == 'BUY' else ('visible_reason',),
        policy_warnings=warnings,
        classification_reasons=('classification_visible',),
        classification_warnings=warnings,
    )


def _screener_result(rows: tuple[MinimalScreenerTableRow, ...]) -> MinimalScreenerResult:
    signal_counts = {}
    type_counts = {}
    for row in rows:
        signal_counts[row.trade_signal] = signal_counts.get(row.trade_signal, 0) + 1
        type_counts[row.candidate_type] = type_counts.get(row.candidate_type, 0) + 1
    return MinimalScreenerResult(
        universe_id='NORWAY_V2',
        universe_source='explicit_tickers',
        benchmark_ticker='OSEBX.OL',
        price_table='price_history_v2',
        data_source='yahoo',
        close_input_source='adjusted_close',
        benchmark_alignment_date='2026-09-09',
        benchmark_lag_warning_count=0,
        ranking_engine_id=BASELINE_RANKING_ENGINE_ID,
        policy_engine_id=TRADE_POLICY_ENGINE_ID,
        classification_engine_id=CANDIDATE_TYPE_ENGINE_ID,
        input_universe_count=4,
        structural_eligible_count=4,
        structural_rejected_count=0,
        feature_complete_count=len(rows),
        feature_incomplete_count=0,
        ranked_count=len(rows),
        trade_signal_counts=signal_counts,
        candidate_type_counts=type_counts,
        structural_rejection_counts_by_reason={},
        feature_missing_reason_counts={},
        signal_type_matrix=(
            CandidateSignalMatrixRow(candidate_type='Stable Leader', trade_signal='BUY', count=1),
        ),
        rows=rows,
    )


def _seed_result(*, provider_error: str | None = None, invalid_ticker: str = 'SUBC.OL') -> MarketDataWriteTestResult:
    write_result = MarketDataWriteResult(
        row_count_before=0,
        row_count_after=12,
        inserted_count=12,
        updated_count=0,
        skipped_count=0,
        invalid_row_count=1 if invalid_ticker else 0,
        invalid_reasons={'raw_low_higher_than_raw_close': 1} if invalid_ticker else {},
        tolerated_warnings={'raw_high_lower_than_raw_close_tolerated': 2},
        rows=(
            MarketDataDryRunValidationRow(
                ticker=invalid_ticker,
                price_date='2026-09-09',
                data_source='yahoo',
                valid=False,
                reasons=('raw_low_higher_than_raw_close',),
                validation_warnings=(),
                action='invalid',
            ),
        ) if invalid_ticker else (),
    )
    return MarketDataWriteTestResult(
        db_path=Path('/tmp/tradetool_candidate_quality_unit.sqlite'),
        source_name='yahoo',
        requested_tickers=('DNB.OL', 'JIN.OL', 'NBX.OL', 'SUBC.OL', 'OSEBX.OL'),
        fetched_tickers=() if provider_error else ('DNB.OL', 'JIN.OL', 'NBX.OL', 'SUBC.OL', 'OSEBX.OL'),
        missing_tickers=(),
        provider_warning=None,
        provider_error=provider_error,
        write_result=write_result,
        ticker_statuses=(),
        written_rows=(),
        invalid_rows=(),
        generated_at_utc='2026-09-09T00:00:00Z',
        partial_invalid_skip_enabled=True,
    )


def _universe_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE universe_cache (universe_key TEXT, tickers_json TEXT)')
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?)',
            ('NORWAY_V2', json.dumps(['DNB.OL', 'JIN.OL', 'NBX.OL', 'SUBC.OL'])),
        )


class CandidateQualityComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_root = Path('/tmp')

    def tearDown(self) -> None:
        for pattern in ('tradetool_candidate_quality_unit*', 'candidate_quality_unit*'):
            for path in self.temp_root.glob(pattern):
                if path.is_dir():
                    for child in path.iterdir():
                        child.unlink()
                    path.rmdir()
                elif path.exists():
                    path.unlink()

    def test_buy_watch_red_flag_detection_distinguishes_serious_and_warning_flags(self) -> None:
        clean = _row(ticker='DNB.OL', raw_rank=1, raw_score=12.0, trade_signal='BUY', candidate_type='Stable Leader').to_dict()
        warning = _row(
            ticker='JIN.OL',
            raw_rank=2,
            raw_score=11.5,
            trade_signal='WATCH',
            candidate_type='Early Breakout',
            traded_value=500_000.0,
        ).to_dict()
        serious = _row(
            ticker='BAD.OL',
            raw_rank=3,
            raw_score=11.0,
            trade_signal='BUY',
            candidate_type='Stable Leader',
            return_3m=-0.01,
        ).to_dict()
        self.assertEqual(build_red_flags(clean), ())
        self.assertIn('moderate_liquidity', build_red_flags(warning))
        self.assertIn('negative_3m_return', build_red_flags(serious))

    def test_baseline_overlap_calculation_uses_available_evidence_only(self) -> None:
        evidence_dir = self.temp_root / 'candidate_quality_unit_evidence'
        evidence_dir.mkdir()
        with (evidence_dir / 'ose_method_comparison_top20.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv_writer(handle)
            writer.writerow(['method', 'ticker', 'method_rank'])
            writer.writerow(['main_momentum', 'DNB.OL', '1'])
            writer.writerow(['current_ml_runtime', 'NBX.OL', '2'])
            writer.writerow(['current_ml_runtime', 'MISSING.OL', '3'])
        rows = (
            _row(ticker='DNB.OL', raw_rank=1, raw_score=12, trade_signal='BUY', candidate_type='Stable Leader').to_dict(),
            _row(ticker='NBX.OL', raw_rank=2, raw_score=3, trade_signal='AVOID', candidate_type='Reject').to_dict(),
        )
        overlap, missing = build_baseline_overlap_rows(current_rows=rows, evidence_dir=evidence_dir)
        self.assertEqual(len(overlap), 3)
        self.assertTrue(overlap[0]['in_current_top20'])
        self.assertEqual(overlap[1]['current_trade_signal'], 'AVOID')
        self.assertIn(str(evidence_dir / 'focus_ticker_baseline.csv'), missing)

    def test_missing_baseline_evidence_is_reported_honestly(self) -> None:
        evidence_dir = self.temp_root / 'candidate_quality_unit_missing_evidence'
        evidence_dir.mkdir()
        overlap, missing = build_baseline_overlap_rows(current_rows=(), evidence_dir=evidence_dir)
        self.assertEqual(overlap, ())
        self.assertIn(str(evidence_dir / 'ose_method_comparison_top20.csv'), missing)

    def test_focus_ticker_report_includes_present_and_missing_cases(self) -> None:
        rows = (
            _row(ticker='DNB.OL', raw_rank=1, raw_score=12, trade_signal='BUY', candidate_type='Stable Leader').to_dict(),
        )
        focus = build_focus_ticker_rows(
            focus_tickers=('DNB.OL', 'MISSING.OL'),
            current_rows=rows,
            requested_tickers=('DNB.OL',),
            feature_incomplete_tickers=(),
        )
        self.assertEqual(focus[0].status, 'present_ranked')
        self.assertEqual(focus[1].status, 'missing_from_universe_or_provider')

    def test_prior_bad_names_are_checked_when_present_and_decision_can_continue(self) -> None:
        seed = _seed_result()
        screener = _screener_result((
            _row(ticker='DNB.OL', raw_rank=1, raw_score=12, trade_signal='BUY', candidate_type='Stable Leader'),
            _row(ticker='JIN.OL', raw_rank=2, raw_score=11, trade_signal='WATCH', candidate_type='Early Breakout', traded_value=500_000),
            _row(ticker='NBX.OL', raw_rank=3, raw_score=4, trade_signal='AVOID', candidate_type='Reject', return_3m=-0.2),
        ))
        decision, reasons = recommend_next_action(
            seed_result=seed,
            screener_result=screener,
            red_flag_rows=(),
            focus_rows=(),
            prior_bad_rows=({'ticker': 'NBX.OL', 'trade_signal': 'AVOID'},),
        )
        self.assertEqual(decision, DECISION_CONTINUE)
        self.assertIn('buy_watch_clean_prior_bad_blocked_rejected_focus_explained', reasons)

    def test_decision_recommendation_blocks_serious_buy_watch_red_flags(self) -> None:
        seed = _seed_result()
        screener = _screener_result((
            _row(ticker='BAD.OL', raw_rank=1, raw_score=12, trade_signal='BUY', candidate_type='Stable Leader'),
            _row(ticker='OK1.OL', raw_rank=2, raw_score=11, trade_signal='BUY', candidate_type='Stable Leader'),
            _row(ticker='OK2.OL', raw_rank=3, raw_score=10, trade_signal='WATCH', candidate_type='Early Breakout'),
        ))
        decision, _ = recommend_next_action(
            seed_result=seed,
            screener_result=screener,
            red_flag_rows=({'ticker': 'BAD.OL', 'trade_signal': 'BUY', 'severity': 'serious'},),
            focus_rows=(),
            prior_bad_rows=(),
        )
        self.assertEqual(decision, DECISION_BUY_WATCH)

    def test_build_result_runs_seed_screener_baseline_and_chart_without_formula_changes(self) -> None:
        universe_db = self.temp_root / 'candidate_quality_unit_universe.sqlite'
        _universe_db(universe_db)
        evidence_dir = self.temp_root / 'candidate_quality_unit_evidence_full'
        evidence_dir.mkdir()
        with (evidence_dir / 'ose_method_comparison_top20.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv_writer(handle)
            writer.writerow(['method', 'ticker', 'method_rank'])
            writer.writerow(['main_momentum', 'DNB.OL', '1'])
        screener = _screener_result((
            _row(ticker='DNB.OL', raw_rank=1, raw_score=12, trade_signal='BUY', candidate_type='Stable Leader'),
            _row(ticker='NBX.OL', raw_rank=2, raw_score=4, trade_signal='AVOID', candidate_type='Reject', return_3m=-0.2),
        ))
        result = build_candidate_quality_comparison(
            db_path=self.temp_root / 'candidate_quality_unit_build.sqlite',
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            start_date=date(2025, 6, 19),
            source_name='yahoo',
            allow_test_db_write=True,
            allow_partial_invalid_skip=True,
            universe_db_path=universe_db,
            evidence_dir=evidence_dir,
            seed_builder=lambda **kwargs: _seed_result(),
            screener_builder=lambda **kwargs: screener,
            chart_builder=lambda **kwargs: _Chart(warning=None, price_points=(1, 2, 3)),
        )
        self.assertEqual(result.universe_source, 'universe_cache:NORWAY_V2')
        self.assertEqual(result.decision_recommendation, DECISION_CONTINUE)
        self.assertEqual(result.chart_detail_status, 'completed')
        self.assertEqual(result.screener_result.ranking_engine_id, BASELINE_RANKING_ENGINE_ID)

    def test_report_writes_expected_files(self) -> None:
        out_dir = self.temp_root / 'candidate_quality_unit_output'
        result = CandidateQualityFixture.result()
        write_candidate_quality_outputs(result=result, out_dir=out_dir)
        self.assertEqual(sorted(path.name for path in out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))
        summary = json.loads((out_dir / 'candidate_quality_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['decision_recommendation'], DECISION_CONTINUE)

    def test_cli_requires_explicit_db_path_and_does_not_default_to_production_db(self) -> None:
        parser = build_argument_parser()
        db_action = next(action for action in parser._actions if '--db-path' in action.option_strings)
        self.assertTrue(db_action.required)
        self.assertIsNone(db_action.default)

    def test_cli_writes_outputs_and_cleans_temp_db(self) -> None:
        out_dir = self.temp_root / 'candidate_quality_unit_cli_output'
        db_path = self.temp_root / 'candidate_quality_unit_cli.sqlite'
        db_path.write_text('old temp db', encoding='utf-8')
        with patch(
            'tradetool.diagnostics.candidate_quality_comparison_cli.build_candidate_quality_comparison',
            return_value=CandidateQualityFixture.result(),
        ):
            exit_code = candidate_quality_cli_main([
                '--db-path', str(db_path),
                '--universe-id', 'NORWAY_V2',
                '--benchmark-ticker', 'OSEBX.OL',
                '--start-date', '2025-06-19',
                '--source', 'yahoo',
                '--allow-test-db-write',
                '--allow-partial-invalid-skip',
                '--out-dir', str(out_dir),
            ])
        self.assertEqual(exit_code, 0)
        self.assertFalse(db_path.exists())
        self.assertEqual(sorted(path.name for path in out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))

    def test_temp_db_cleanup_is_limited_to_tmp_files(self) -> None:
        path = self.temp_root / 'candidate_quality_unit_cleanup.sqlite'
        path.write_text('x', encoding='utf-8')
        self.assertTrue(remove_temp_db_if_allowed(path))
        self.assertFalse(path.exists())

    def test_no_ranking_policy_candidate_type_ml_or_holdings_logic_is_added(self) -> None:
        self.assertEqual(BASELINE_RANKING_ENGINE_ID, 'baseline_v0_price_volume_rs')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(MAX_BUY_RAW_RANK, 20)
        self.assertEqual(MIN_ACCEPTABLE_TRADED_VALUE, 750_000.0)
        self.assertEqual(TradeSignal.BUY.value, 'BUY')
        self.assertEqual(CandidateType.STABLE_LEADER.value, 'Stable Leader')
        source = Path('src/tradetool/diagnostics/candidate_quality_comparison.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('ml_runs', source)
        self.assertNotIn('holdings', source)


def csv_writer(handle):
    import csv

    return csv.writer(handle)


class CandidateQualityFixture:
    @staticmethod
    def result():
        rows = (
            _row(ticker='DNB.OL', raw_rank=1, raw_score=12, trade_signal='BUY', candidate_type='Stable Leader').to_dict(),
        )
        seed = _seed_result(invalid_ticker='')
        screener = _screener_result((
            _row(ticker='DNB.OL', raw_rank=1, raw_score=12, trade_signal='BUY', candidate_type='Stable Leader'),
        ))
        return type('ResultProxy', (), {
            'universe_id': 'NORWAY_V2',
            'universe_source': 'universe_cache:NORWAY_V2',
            'benchmark_ticker': 'OSEBX.OL',
            'data_source': 'yahoo',
            'start_date': '2025-06-19',
            'stock_ticker_count': 1,
            'seed_result': seed,
            'screener_result': screener,
            'current_top_candidates': rows,
            'buy_watch_review_rows': rows,
            'focus_rows': (),
            'baseline_overlap_rows': (),
            'red_flag_rows': (),
            'prior_bad_rows': (),
            'missing_evidence_files': (),
            'decision_recommendation': DECISION_CONTINUE,
            'decision_reasons': ('buy_watch_clean_prior_bad_blocked_rejected_focus_explained',),
            'chart_detail_status': 'completed',
            'chart_detail_warning': None,
            'chart_detail_point_count': 3,
            'generated_at_utc': '2026-09-09T00:00:00Z',
            'to_summary_dict': lambda self: {
                'decision_recommendation': DECISION_CONTINUE,
                'seed': seed.to_summary_dict(),
                'screener': {
                    'ranked_count': 1,
                    'feature_complete_count': 1,
                    'feature_incomplete_count': 0,
                    'close_input_source': 'adjusted_close',
                    'benchmark_alignment_date': '2026-09-09',
                    'benchmark_lag_warning_count': 0,
                    'trade_signal_counts': {'BUY': 1},
                    'candidate_type_counts': {'Stable Leader': 1},
                    'price_table': 'price_history_v2',
                },
                'quality': {
                    'buy_watch_count': 1,
                    'serious_buy_watch_red_flag_count': 0,
                    'warning_buy_watch_red_flag_count': 0,
                    'prior_bad_buy_count': 0,
                    'unexplained_rejected_focus_count': 0,
                },
                'baseline': {
                    'current_top20_overlap_by_method': {},
                    'current_top30_overlap_by_method': {},
                    'missing_evidence_files': [],
                    'overlap_row_count': 0,
                },
            },
        })()


if __name__ == '__main__':
    unittest.main()

from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.contracts.enums import CandidateType, TradeSignal
from tradetool.diagnostics.baseline_ranking import BaselineRankingDiagnosticsResult
from tradetool.diagnostics.candidate_type import CandidateTypeDiagnosticsResult
from tradetool.diagnostics.candidate_type_sanity import (
    BAD_HIGH_RISK_TICKERS,
    DECISION_BLOCKED,
    DECISION_CONTINUE,
    DECISION_REVISE_CANDIDATE_TYPES,
    FOCUS_TICKERS,
    build_candidate_type_sanity_from_candidate,
    build_candidate_type_sanity_report,
    write_candidate_type_sanity_outputs,
)
from tradetool.diagnostics.candidate_type_sanity_cli import main as candidate_type_sanity_cli_main
from tradetool.diagnostics.trade_policy import TradePolicyDiagnosticsResult
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID, CandidateTypeDiagnosticsRow
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


def _insert_rows(connection: sqlite3.Connection, ticker: str, closes: list[float], *, last_date: date, volume: float = 100.0) -> None:
    rows = []
    start = last_date - timedelta(days=len(closes) - 1)
    for index, close_value in enumerate(closes):
        current_date = start + timedelta(days=index)
        rows.append((ticker, current_date.isoformat(), close_value, close_value + 1.0, close_value - 1.0, close_value, volume + index))
    connection.executemany('INSERT INTO price_history VALUES (?, ?, ?, ?, ?, ?, ?)', rows)


def _build_fixture_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE price_history (ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)')
        connection.execute('CREATE TABLE universe_cache (universe_key TEXT PRIMARY KEY, tickers_json TEXT, source_label TEXT, updated_at TEXT)')
        last_date = date(2025, 1, 1) + timedelta(days=259)
        tickers = [
            'KIT.OL', 'HAUTO.OL', 'MPCC.OL', 'SUBC.OL', 'FRO.OL', 'BWLPG.OL', 'ENDUR.OL', 'VAR.OL', 'NHY.OL', 'AKRBP.OL',
            'ZENA.OL', 'NBX.OL', 'PRS.OL', 'BCS.OL', 'MORLD.OL', 'LEADER.OL', 'BREAK.OL',
        ]
        for offset, ticker in enumerate(tickers):
            slope = 1.35 - (offset * 0.03)
            closes = [100.0 + index * slope for index in range(260)]
            if ticker in {'KIT.OL', 'HAUTO.OL', 'MPCC.OL', 'SUBC.OL', 'FRO.OL', 'BWLPG.OL', 'ZENA.OL', 'NBX.OL'}:
                closes = [100.0 + index * 1.0 for index in range(200)] + [310.0 - index * 1.6 for index in range(60)]
            _insert_rows(connection, ticker, closes, last_date=last_date, volume=2400.0 - offset * 80)
        benchmark = [300.0 + index * 0.4 for index in range(260)]
        _insert_rows(connection, '^OSEAX', benchmark, last_date=last_date, volume=3000.0)
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('NORWAY_V2', json.dumps(tickers), 'fixture', '2026-08-17T00:00:00Z'),
        )


def _fake_ranking() -> BaselineRankingDiagnosticsResult:
    return BaselineRankingDiagnosticsResult(
        db_path=Path('/tmp/fake.sqlite'),
        universe_id='NORWAY_V2',
        universe_source='universe_cache:NORWAY_V2',
        benchmark_ticker='^OSEAX',
        ranking_engine_id='baseline_v0_price_volume_rs',
        input_universe_count=5,
        structural_eligible_count=5,
        structural_rejected_count=0,
        feature_complete_count=5,
        feature_incomplete_count=0,
        ranked_count=5,
        generated_at_utc='2026-08-17T00:00:00Z',
        rows=(),
    )


def _fake_policy_row(
    ticker: str,
    signal: TradeSignal,
    *,
    raw_rank: int,
    policy_pass: bool,
    reasons: tuple[str, ...],
    warnings: tuple[str, ...] = (),
    above_sma50: bool = True,
    above_sma200: bool = True,
    positive_return_3m: bool = True,
    positive_return_6m: bool = True,
    positive_rs_3m: bool = True,
    positive_rs_6m: bool = True,
    acceptable_drawdown: bool = True,
    acceptable_volatility: bool = True,
    acceptable_traded_value: bool = True,
    moderate_stretch: bool = True,
) -> TradePolicyDiagnosticsRow:
    return TradePolicyDiagnosticsRow(
        ticker=ticker,
        rank_date='2026-08-17',
        ranking_engine_id='baseline_v0_price_volume_rs',
        policy_engine_id=TRADE_POLICY_ENGINE_ID,
        raw_rank=raw_rank,
        raw_score=20.0 - raw_rank,
        trade_signal=signal,
        policy_pass=policy_pass,
        policy_reasons=reasons,
        policy_warnings=warnings,
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
        severe_stretch=False,
        drawdown_252=-0.2,
        volatility_63=0.02,
        average_traded_value_20=2_000_000.0,
        distance_to_sma50=0.05,
        distance_to_sma200=0.2,
    )


def _fake_candidate_row(
    ticker: str,
    signal: TradeSignal,
    candidate_type: CandidateType,
    *,
    raw_rank: int,
    raw_score: float | None = None,
    reasons: tuple[str, ...] = ('diagnostic_reason',),
    warnings: tuple[str, ...] = (),
) -> CandidateTypeDiagnosticsRow:
    return CandidateTypeDiagnosticsRow(
        ticker=ticker,
        rank_date='2026-08-17',
        ranking_engine_id='baseline_v0_price_volume_rs',
        policy_engine_id=TRADE_POLICY_ENGINE_ID,
        classification_engine_id=CANDIDATE_TYPE_ENGINE_ID,
        raw_rank=raw_rank,
        raw_score=(20.0 - raw_rank) if raw_score is None else raw_score,
        trade_signal=signal,
        candidate_type=candidate_type,
        classification_reasons=reasons,
        classification_warnings=warnings,
    )


def _fake_candidate_result(
    candidate_rows: tuple[CandidateTypeDiagnosticsRow, ...],
    policy_rows: tuple[TradePolicyDiagnosticsRow, ...],
) -> CandidateTypeDiagnosticsResult:
    candidate_counts: dict[str, int] = {}
    signal_counts: dict[str, int] = {}
    for row in candidate_rows:
        candidate_counts[row.candidate_type.value] = candidate_counts.get(row.candidate_type.value, 0) + 1
        signal_counts[row.trade_signal.value] = signal_counts.get(row.trade_signal.value, 0) + 1
    policy = TradePolicyDiagnosticsResult(
        ranking=_fake_ranking(),
        policy_engine_id=TRADE_POLICY_ENGINE_ID,
        generated_at_utc='2026-08-17T00:00:00Z',
        rows=policy_rows,
        signal_counts=signal_counts,
        policy_pass_count=sum(1 for row in policy_rows if row.policy_pass),
    )
    return CandidateTypeDiagnosticsResult(
        policy=policy,
        classification_engine_id=CANDIDATE_TYPE_ENGINE_ID,
        generated_at_utc='2026-08-17T00:00:00Z',
        rows=candidate_rows,
        candidate_type_counts=candidate_counts,
        trade_signal_counts=signal_counts,
    )


class CandidateTypeSanityDiagnosticsTests(unittest.TestCase):
    def test_cli_writes_exactly_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'sanity_output'
            _build_fixture_db(db_path)
            exit_code = candidate_type_sanity_cli_main([
                '--db-path', str(db_path), '--universe-id', 'NORWAY_V2', '--benchmark-ticker', '^OSEAX', '--out-dir', str(out_dir),
            ])
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                sorted(path.name for path in out_dir.iterdir()),
                [
                    'candidate_type_counts.csv',
                    'candidate_type_sanity_summary.json',
                    'candidate_type_sanity_summary.md',
                    'candidate_type_signal_matrix.csv',
                    'focus_candidate_type_audit.csv',
                    'top20_candidate_type_audit.csv',
                ],
            )

    def test_candidate_type_counts_match_row_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'sanity_output'
            _build_fixture_db(db_path)
            result = build_candidate_type_sanity_report(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            write_candidate_type_sanity_outputs(result=result, out_dir=out_dir)
            summary = json.loads((out_dir / 'candidate_type_sanity_summary.json').read_text(encoding='utf-8'))
            counts = {}
            for row in result.candidate.rows:
                counts[row.candidate_type.value] = counts.get(row.candidate_type.value, 0) + 1
            self.assertEqual(summary['candidate_type_counts'], counts)

    def test_candidate_type_by_signal_matrix_is_correct(self) -> None:
        candidate = _fake_candidate_result(
            (
                _fake_candidate_row('AAA.OL', TradeSignal.BUY, CandidateType.STABLE_LEADER, raw_rank=1),
                _fake_candidate_row('BBB.OL', TradeSignal.WATCH, CandidateType.EARLY_BREAKOUT, raw_rank=2),
                _fake_candidate_row('CCC.OL', TradeSignal.AVOID, CandidateType.REJECT, raw_rank=3),
            ),
            (
                _fake_policy_row('AAA.OL', TradeSignal.BUY, raw_rank=1, policy_pass=True, reasons=('strong_trend_profile',)),
                _fake_policy_row('BBB.OL', TradeSignal.WATCH, raw_rank=2, policy_pass=True, reasons=('watchlist_candidate',)),
                _fake_policy_row('CCC.OL', TradeSignal.AVOID, raw_rank=3, policy_pass=False, reasons=('deep_drawdown',), acceptable_drawdown=False),
            ),
        )
        result = build_candidate_type_sanity_from_candidate(candidate)
        matrix = {(row.candidate_type, row.trade_signal): row.count for row in result.matrix_rows}
        self.assertEqual(matrix[('Stable Leader', 'BUY')], 1)
        self.assertEqual(matrix[('Early Breakout', 'WATCH')], 1)
        self.assertEqual(matrix[('Reject', 'AVOID')], 1)

    def test_top20_audit_includes_diagnostic_notes(self) -> None:
        candidate = _fake_candidate_result(
            (_fake_candidate_row('AAA.OL', TradeSignal.BUY, CandidateType.STABLE_LEADER, raw_rank=1),),
            (_fake_policy_row('AAA.OL', TradeSignal.BUY, raw_rank=1, policy_pass=True, reasons=('strong_trend_profile',)),),
        )
        result = build_candidate_type_sanity_from_candidate(candidate)
        self.assertTrue(result.top20_rows[0].diagnostic_note)

    def test_focus_audit_includes_requested_focus_tickers_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'sanity_output'
            _build_fixture_db(db_path)
            result = build_candidate_type_sanity_report(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            write_candidate_type_sanity_outputs(result=result, out_dir=out_dir)
            with (out_dir / 'focus_candidate_type_audit.csv').open('r', encoding='utf-8', newline='') as handle:
                rows = list(csv.DictReader(handle))
            tickers = {row['ticker'] for row in rows}
            self.assertTrue(set(FOCUS_TICKERS).issubset(tickers))

    def test_bad_high_risk_audit_detects_buy_or_stable_leader(self) -> None:
        candidate = _fake_candidate_result(
            (_fake_candidate_row('NBX.OL', TradeSignal.BUY, CandidateType.STABLE_LEADER, raw_rank=4),),
            (_fake_policy_row('NBX.OL', TradeSignal.BUY, raw_rank=4, policy_pass=True, reasons=('strong_trend_profile',)),),
        )
        result = build_candidate_type_sanity_from_candidate(candidate, eligibility_rows={'NBX.OL': object()})
        row = next(row for row in result.bad_high_risk_rows if row.ticker == 'NBX.OL')
        self.assertEqual(row.trade_signal, 'BUY')
        self.assertEqual(row.candidate_type, 'Stable Leader')

    def test_bad_high_risk_buy_or_stable_leader_causes_revise_candidate_type_rules(self) -> None:
        candidate = _fake_candidate_result(
            (_fake_candidate_row('NBX.OL', TradeSignal.AVOID, CandidateType.STABLE_LEADER, raw_rank=4),),
            (_fake_policy_row('NBX.OL', TradeSignal.AVOID, raw_rank=4, policy_pass=False, reasons=('deep_drawdown',), acceptable_drawdown=False),),
        )
        result = build_candidate_type_sanity_from_candidate(candidate, eligibility_rows={'NBX.OL': object()})
        self.assertEqual(result.decision_recommendation, DECISION_REVISE_CANDIDATE_TYPES)

    def test_no_extended_runner_creates_warning_but_does_not_automatically_block(self) -> None:
        candidate = _fake_candidate_result(
            (
                _fake_candidate_row('AAA.OL', TradeSignal.BUY, CandidateType.STABLE_LEADER, raw_rank=1),
                _fake_candidate_row('BBB.OL', TradeSignal.WATCH, CandidateType.EARLY_BREAKOUT, raw_rank=2),
            ),
            (
                _fake_policy_row('AAA.OL', TradeSignal.BUY, raw_rank=1, policy_pass=True, reasons=('strong_trend_profile',)),
                _fake_policy_row('BBB.OL', TradeSignal.WATCH, raw_rank=2, policy_pass=True, reasons=('watchlist_candidate',)),
            ),
        )
        result = build_candidate_type_sanity_from_candidate(candidate)
        self.assertIn('no Extended Runners exist', result.warnings)
        self.assertEqual(result.decision_recommendation, DECISION_CONTINUE)

    def test_row_count_mismatch_causes_blocked_insufficient_evidence(self) -> None:
        candidate = _fake_candidate_result(
            (_fake_candidate_row('AAA.OL', TradeSignal.BUY, CandidateType.STABLE_LEADER, raw_rank=1),),
            (
                _fake_policy_row('AAA.OL', TradeSignal.BUY, raw_rank=1, policy_pass=True, reasons=('strong_trend_profile',)),
                _fake_policy_row('BBB.OL', TradeSignal.WATCH, raw_rank=2, policy_pass=True, reasons=('watchlist_candidate',)),
            ),
        )
        result = build_candidate_type_sanity_from_candidate(candidate)
        self.assertEqual(result.decision_recommendation, DECISION_BLOCKED)

    def test_report_contains_no_ml_score_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'sanity_output'
            _build_fixture_db(db_path)
            result = build_candidate_type_sanity_report(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            write_candidate_type_sanity_outputs(result=result, out_dir=out_dir)
            summary = json.loads((out_dir / 'candidate_type_sanity_summary.json').read_text(encoding='utf-8'))
            with (out_dir / 'top20_candidate_type_audit.csv').open('r', encoding='utf-8', newline='') as handle:
                header = csv.DictReader(handle).fieldnames or []
            self.assertNotIn('ml_score', summary)
            self.assertNotIn('ml_score', header)

    def test_no_holdings_signal_logic_is_added(self) -> None:
        module_source = Path('src/tradetool/diagnostics/candidate_type_sanity.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('holdings_signal', module_source)
        self.assertNotIn('tradetool.holdings', module_source)

    def test_candidate_type_rules_are_not_changed(self) -> None:
        module_source = Path('src/tradetool/policy/candidate_type.py').read_text(encoding='utf-8')
        self.assertIn("CANDIDATE_TYPE_ENGINE_ID = 'candidate_type_v0_diagnostic'", module_source)
        self.assertIn('def _classify_row', module_source)

    def test_no_trade_policy_thresholds_are_changed(self) -> None:
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertEqual(MIN_ACCEPTABLE_DRAWDOWN, -0.40)
        self.assertEqual(MAX_ACCEPTABLE_VOLATILITY, 0.04)
        self.assertEqual(MAX_MODERATE_VOLATILITY, 0.06)
        self.assertEqual(MIN_ACCEPTABLE_TRADED_VALUE, 750_000.0)
        self.assertEqual(MIN_MODERATE_TRADED_VALUE, 250_000.0)
        self.assertEqual(MAX_BUY_DISTANCE_TO_SMA50, 0.22)
        self.assertEqual(MAX_WATCH_DISTANCE_TO_SMA50, 0.35)
        self.assertEqual(MAX_BUY_DISTANCE_TO_SMA200, 0.45)
        self.assertEqual(MAX_WATCH_DISTANCE_TO_SMA200, 0.70)
        self.assertEqual(MAX_BUY_RAW_RANK, 20)
        self.assertEqual(MAX_WATCH_RAW_RANK, 80)

    def test_ranking_formula_is_not_changed(self) -> None:
        ranking_source = Path('src/tradetool/ranking/baseline.py').read_text(encoding='utf-8')
        self.assertIn("BASELINE_RANKING_ENGINE_ID = 'baseline_v0_price_volume_rs'", ranking_source)

    def test_bad_high_risk_constant_set_is_stable(self) -> None:
        self.assertEqual(BAD_HIGH_RISK_TICKERS, ('ZENA.OL', 'NBX.OL', 'PRS.OL', 'BCS.OL', 'MORLD.OL'))

from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.contracts.enums import TradeSignal
from tradetool.diagnostics.baseline_ranking import BaselineRankingDiagnosticsResult
from tradetool.diagnostics.trade_policy import TradePolicyDiagnosticsResult
from tradetool.diagnostics.trade_policy_sanity import (
    DECISION_FIX,
    DECISION_REVISE,
    GATE_FIELDS,
    build_trade_policy_sanity_from_policy,
    build_trade_policy_sanity_report,
    write_trade_policy_sanity_outputs,
)
from tradetool.diagnostics.trade_policy_sanity_cli import main as trade_policy_sanity_cli_main
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
        connection.execute(
            'CREATE TABLE price_history (ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)'
        )
        connection.execute(
            'CREATE TABLE universe_cache (universe_key TEXT PRIMARY KEY, tickers_json TEXT, source_label TEXT, updated_at TEXT)'
        )
        last_date = date(2025, 1, 1) + timedelta(days=259)
        tickers = ['KIT.OL', 'HAUTO.OL', 'MPCC.OL', 'SUBC.OL', 'FRO.OL', 'BWLPG.OL', 'ENDUR.OL', 'VAR.OL', 'NHY.OL', 'AKRBP.OL']
        for offset, ticker in enumerate(tickers):
            slope = 1.4 - (offset * 0.05)
            closes = [100.0 + index * slope for index in range(260)]
            if ticker in {'KIT.OL', 'HAUTO.OL', 'MPCC.OL', 'SUBC.OL', 'FRO.OL', 'BWLPG.OL'}:
                closes = [100.0 + index * 1.1 for index in range(200)] + [320.0 - index * 1.7 for index in range(60)]
            _insert_rows(connection, ticker, closes, last_date=last_date, volume=2200.0 - offset * 120)
        benchmark = [300.0 + index * 0.4 for index in range(260)]
        _insert_rows(connection, '^OSEAX', benchmark, last_date=last_date, volume=3000.0)
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            (
                'NORWAY_V2',
                json.dumps(tickers),
                'fixture',
                '2026-08-16T00:00:00Z',
            ),
        )


def _fake_ranking() -> BaselineRankingDiagnosticsResult:
    return BaselineRankingDiagnosticsResult(
        db_path=Path('/tmp/fake.sqlite'),
        universe_id='NORWAY_V2',
        universe_source='universe_cache:NORWAY_V2',
        benchmark_ticker='^OSEAX',
        ranking_engine_id='baseline_v0_price_volume_rs',
        input_universe_count=3,
        structural_eligible_count=3,
        structural_rejected_count=0,
        feature_complete_count=3,
        feature_incomplete_count=0,
        ranked_count=3,
        generated_at_utc='2026-08-16T00:00:00Z',
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
    policy_engine_id: str = TRADE_POLICY_ENGINE_ID,
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
    severe_stretch: bool = False,
) -> TradePolicyDiagnosticsRow:
    return TradePolicyDiagnosticsRow(
        ticker=ticker,
        rank_date='2026-08-16',
        ranking_engine_id='baseline_v0_price_volume_rs',
        policy_engine_id=policy_engine_id,
        raw_rank=raw_rank,
        raw_score=10.0 - raw_rank,
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
        severe_stretch=severe_stretch,
        drawdown_252=-0.2,
        volatility_63=0.02,
        average_traded_value_20=2_000_000.0,
        distance_to_sma50=0.05,
        distance_to_sma200=0.2,
    )


def _fake_policy_result(rows: tuple[TradePolicyDiagnosticsRow, ...]) -> TradePolicyDiagnosticsResult:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.trade_signal.value] = counts.get(row.trade_signal.value, 0) + 1
    return TradePolicyDiagnosticsResult(
        ranking=_fake_ranking(),
        policy_engine_id=TRADE_POLICY_ENGINE_ID,
        generated_at_utc='2026-08-16T00:00:00Z',
        rows=rows,
        signal_counts=counts,
        policy_pass_count=sum(1 for row in rows if row.policy_pass),
    )


class TradePolicySanityDiagnosticsTests(unittest.TestCase):
    def test_sanity_cli_writes_exactly_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'sanity_output'
            _build_fixture_db(db_path)
            exit_code = trade_policy_sanity_cli_main(
                [
                    '--db-path',
                    str(db_path),
                    '--universe-id',
                    'NORWAY_V2',
                    '--benchmark-ticker',
                    '^OSEAX',
                    '--out-dir',
                    str(out_dir),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                sorted(path.name for path in out_dir.iterdir()),
                [
                    'focus_policy_audit.csv',
                    'gate_failure_counts.csv',
                    'top20_policy_audit.csv',
                    'trade_policy_sanity_summary.json',
                    'trade_policy_sanity_summary.md',
                ],
            )

    def test_signal_distribution_counts_match_policy_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'sanity_output'
            _build_fixture_db(db_path)
            result = build_trade_policy_sanity_report(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            write_trade_policy_sanity_outputs(result=result, out_dir=out_dir)
            summary = json.loads((out_dir / 'trade_policy_sanity_summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['policy_row_count'], len(result.policy.rows))
            self.assertEqual(summary['signal_counts'], result.policy.signal_counts)

    def test_top20_audit_includes_not_buy_explanation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'sanity_output'
            _build_fixture_db(db_path)
            result = build_trade_policy_sanity_report(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            write_trade_policy_sanity_outputs(result=result, out_dir=out_dir)
            with (out_dir / 'top20_policy_audit.csv').open('r', encoding='utf-8', newline='') as handle:
                rows = list(csv.DictReader(handle))
            self.assertIn('not_buy_explanation', rows[0])
            self.assertTrue(any(row['not_buy_explanation'] for row in rows if row['trade_signal'] != 'BUY'))

    def test_focus_audit_includes_requested_focus_tickers_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'sanity_output'
            _build_fixture_db(db_path)
            result = build_trade_policy_sanity_report(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            write_trade_policy_sanity_outputs(result=result, out_dir=out_dir)
            with (out_dir / 'focus_policy_audit.csv').open('r', encoding='utf-8', newline='') as handle:
                rows = list(csv.DictReader(handle))
            tickers = {row['ticker'] for row in rows}
            self.assertTrue({'KIT.OL', 'HAUTO.OL', 'MPCC.OL', 'SUBC.OL', 'FRO.OL', 'BWLPG.OL'}.issubset(tickers))

    def test_gate_failure_counts_are_aggregated_correctly(self) -> None:
        rows = (
            _fake_policy_row('AAA.OL', TradeSignal.AVOID, raw_rank=1, policy_pass=False, reasons=('deep_drawdown',), acceptable_drawdown=False),
            _fake_policy_row('BBB.OL', TradeSignal.WATCH, raw_rank=2, policy_pass=True, reasons=('watchlist_candidate',), acceptable_traded_value=False),
            _fake_policy_row('KIT.OL', TradeSignal.REVIEW, raw_rank=3, policy_pass=False, reasons=('below_sma50',), above_sma50=False),
        )
        result = build_trade_policy_sanity_from_policy(_fake_policy_result(rows))
        counts = {(row.scope, row.gate_name): row.failed_count for row in result.gate_failure_counts}
        self.assertEqual(counts[('all_ranked_rows', 'acceptable_drawdown')], 1)
        self.assertEqual(counts[('all_ranked_rows', 'acceptable_traded_value')], 1)
        self.assertEqual(counts[('all_ranked_rows', 'above_sma50')], 1)
        self.assertEqual(counts[('avoid_rows_only', 'acceptable_drawdown')], 1)

    def test_zero_buy_causes_revise_trade_policy_thresholds(self) -> None:
        rows = (
            _fake_policy_row('AAA.OL', TradeSignal.WATCH, raw_rank=1, policy_pass=True, reasons=('watchlist_candidate',)),
            _fake_policy_row('BBB.OL', TradeSignal.AVOID, raw_rank=2, policy_pass=False, reasons=('deep_drawdown',), acceptable_drawdown=False),
        )
        result = build_trade_policy_sanity_from_policy(_fake_policy_result(rows))
        self.assertEqual(result.decision_recommendation, DECISION_REVISE)

    def test_missing_or_inconsistent_policy_fields_causes_fix_trade_policy_bug(self) -> None:
        rows = (
            _fake_policy_row('AAA.OL', TradeSignal.BUY, raw_rank=1, policy_pass=False, reasons=('strong_trend_profile',)),
        )
        result = build_trade_policy_sanity_from_policy(_fake_policy_result(rows))
        self.assertEqual(result.decision_recommendation, DECISION_FIX)

    def test_no_trade_policy_thresholds_are_changed(self) -> None:
        self.assertEqual(MIN_ACCEPTABLE_DRAWDOWN, -0.25)
        self.assertEqual(MAX_ACCEPTABLE_VOLATILITY, 0.03)
        self.assertEqual(MAX_MODERATE_VOLATILITY, 0.05)
        self.assertEqual(MIN_ACCEPTABLE_TRADED_VALUE, 1_000_000.0)
        self.assertEqual(MIN_MODERATE_TRADED_VALUE, 250_000.0)
        self.assertEqual(MAX_BUY_DISTANCE_TO_SMA50, 0.15)
        self.assertEqual(MAX_WATCH_DISTANCE_TO_SMA50, 0.30)
        self.assertEqual(MAX_BUY_DISTANCE_TO_SMA200, 0.35)
        self.assertEqual(MAX_WATCH_DISTANCE_TO_SMA200, 0.60)
        self.assertEqual(MAX_BUY_RAW_RANK, 20)
        self.assertEqual(MAX_WATCH_RAW_RANK, 60)

    def test_no_candidate_type_or_ml_score_is_produced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'sanity_output'
            _build_fixture_db(db_path)
            result = build_trade_policy_sanity_report(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            write_trade_policy_sanity_outputs(result=result, out_dir=out_dir)
            summary = json.loads((out_dir / 'trade_policy_sanity_summary.json').read_text(encoding='utf-8'))
            with (out_dir / 'top20_policy_audit.csv').open('r', encoding='utf-8', newline='') as handle:
                header = csv.DictReader(handle).fieldnames or []
            forbidden = {'candidate_type', 'ml_score'}
            self.assertTrue(forbidden.isdisjoint(summary.keys()))
            self.assertTrue(forbidden.isdisjoint(header))

    def test_no_holdings_signal_logic_is_added(self) -> None:
        module_source = Path('src/tradetool/diagnostics/trade_policy_sanity.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('holdings_signal', module_source)
        self.assertNotIn('tradetool.holdings', module_source)

    def test_gate_fields_list_is_stable(self) -> None:
        self.assertEqual(
            GATE_FIELDS,
            (
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
            ),
        )

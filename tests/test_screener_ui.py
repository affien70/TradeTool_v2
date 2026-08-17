from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
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
)
from tradetool.ui.screener import build_minimal_screener_result


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
        tickers = ['LEADER.OL', 'RUNNER.OL', 'REBOUND.OL', 'BREAKOUT.OL', 'REJECT.OL']
        for offset, ticker in enumerate(tickers):
            slope = 1.25 - (offset * 0.08)
            closes = [100.0 + index * slope for index in range(260)]
            if ticker == 'REBOUND.OL':
                closes = [100.0 + index * 1.0 for index in range(200)] + [300.0 - index * 1.5 for index in range(60)]
            _insert_rows(connection, ticker, closes, last_date=last_date, volume=2400.0 - offset * 220)
        outsider = [50.0 + index * 2.0 for index in range(260)]
        _insert_rows(connection, 'AAPL', outsider, last_date=last_date, volume=10_000.0)
        benchmark = [300.0 + index * 0.4 for index in range(260)]
        _insert_rows(connection, '^OSEAX', benchmark, last_date=last_date, volume=3000.0)
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('NORWAY_V2', '["LEADER.OL", "RUNNER.OL", "REBOUND.OL", "BREAKOUT.OL", "REJECT.OL"]', 'fixture', '2026-08-17T00:00:00Z'),
        )


class ScreenerUiOrchestrationTests(unittest.TestCase):
    def test_orchestration_returns_expected_summary_from_synthetic_db(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_fixture_db(db_path)
            result = build_minimal_screener_result(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            self.assertEqual(result.universe_source, 'universe_cache:NORWAY_V2')
            self.assertEqual(result.input_universe_count, 5)
            self.assertEqual(result.ranked_count, len(result.rows))
            self.assertEqual(result.policy_engine_id, TRADE_POLICY_ENGINE_ID)
            self.assertEqual(result.classification_engine_id, CANDIDATE_TYPE_ENGINE_ID)

    def test_orchestration_uses_named_universe_not_all_price_history_tickers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_fixture_db(db_path)
            result = build_minimal_screener_result(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            tickers = {row.ticker for row in result.rows}
            self.assertNotIn('AAPL', tickers)
            self.assertEqual(result.input_universe_count, 5)

    def test_output_preserves_raw_rank_raw_score_trade_signal_and_candidate_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_fixture_db(db_path)
            result = build_minimal_screener_result(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            ranks = [row.raw_rank for row in result.rows]
            self.assertEqual(ranks, list(range(1, len(result.rows) + 1)))
            self.assertTrue(all(isinstance(row.raw_score, float) for row in result.rows))
            self.assertTrue(all(row.trade_signal for row in result.rows))
            self.assertTrue(all(row.candidate_type for row in result.rows))

    def test_default_output_can_filter_out_avoid_without_changing_underlying_row_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_fixture_db(db_path)
            result = build_minimal_screener_result(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            visible = result.visible_rows()
            all_rows = result.visible_rows(include_avoid=True)
            self.assertEqual(len(all_rows), result.ranked_count)
            self.assertLessEqual(len(visible), len(all_rows))
            self.assertTrue(all(row.trade_signal != 'AVOID' for row in visible))

    def test_no_ml_score_field_is_added(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_fixture_db(db_path)
            row = build_minimal_screener_result(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX').rows[0].to_dict()
            self.assertNotIn('ml_score', row)

    def test_no_holdings_signal_field_is_added(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_fixture_db(db_path)
            row = build_minimal_screener_result(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX').rows[0].to_dict()
            self.assertNotIn('holdings_signal', row)

    def test_no_ranking_formula_is_changed(self) -> None:
        ranking_source = Path('src/tradetool/ranking/baseline.py').read_text(encoding='utf-8')
        self.assertIn("BASELINE_RANKING_ENGINE_ID = 'baseline_v0_price_volume_rs'", ranking_source)

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

    def test_no_candidate_type_rules_are_changed(self) -> None:
        candidate_source = Path('src/tradetool/policy/candidate_type.py').read_text(encoding='utf-8')
        self.assertIn("CANDIDATE_TYPE_ENGINE_ID = 'candidate_type_v0_diagnostic'", candidate_source)
        self.assertIn('def _classify_row', candidate_source)

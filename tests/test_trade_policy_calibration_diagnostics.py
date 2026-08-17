from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.contracts.enums import TradeSignal
from tradetool.diagnostics.trade_policy import TradePolicyDiagnosticsResult
from tradetool.diagnostics.trade_policy_calibration import (
    BAD_HIGH_RISK_TICKERS,
    POSITIVE_FOCUS_TICKERS,
    RECOMMEND_APPLY,
    RECOMMEND_REVISE_RANKING,
    THRESHOLD_FIELDS,
    ScenarioSummary,
    TradePolicyCalibrationResult,
    _recommend_scenario,
    _scenario_thresholds,
    build_trade_policy_calibration_report,
    write_trade_policy_calibration_outputs,
)
from tradetool.diagnostics.trade_policy_calibration_cli import main as trade_policy_calibration_cli_main
from tradetool.diagnostics.trade_policy_sanity import FOCUS_TICKERS
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
from tests.test_trade_policy_sanity_diagnostics import _fake_policy_result, _fake_policy_row


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
        tickers = [
            'KIT.OL', 'HAUTO.OL', 'MPCC.OL', 'SUBC.OL', 'FRO.OL', 'BWLPG.OL',
            'ENDUR.OL', 'VAR.OL', 'NHY.OL', 'AKRBP.OL', 'ZENA.OL', 'NBX.OL', 'PRS.OL',
        ]
        for offset, ticker in enumerate(tickers):
            slope = 1.35 - (offset * 0.04)
            closes = [100.0 + index * slope for index in range(260)]
            if ticker in {'KIT.OL', 'HAUTO.OL', 'MPCC.OL', 'SUBC.OL', 'FRO.OL', 'BWLPG.OL'}:
                closes = [100.0 + index * 1.0 for index in range(200)] + [300.0 - index * 1.6 for index in range(60)]
            if ticker in {'ZENA.OL', 'NBX.OL', 'PRS.OL'}:
                closes = [50.0 + index * 0.2 for index in range(150)] + [80.0 - index * 0.35 for index in range(110)]
            _insert_rows(connection, ticker, closes, last_date=last_date, volume=2500.0 - offset * 120)
        benchmark = [300.0 + index * 0.4 for index in range(260)]
        _insert_rows(connection, '^OSEAX', benchmark, last_date=last_date, volume=3000.0)
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            (
                'NORWAY_V2',
                json.dumps(tickers),
                'fixture',
                '2026-08-17T00:00:00Z',
            ),
        )


def _scenario(
    name: str,
    rows: tuple[TradePolicyDiagnosticsRow, ...],
    *,
    bad_buy_or_watch_count: int,
    positive_buy_or_watch_count: int,
) -> ScenarioSummary:
    signal_counts: dict[str, int] = {}
    for row in rows:
        signal_counts[row.trade_signal.value] = signal_counts.get(row.trade_signal.value, 0) + 1
    top20 = tuple()
    focus_rows = tuple()
    bad_rows = tuple()
    return ScenarioSummary(
        name=name,
        rows=rows,
        signal_counts=signal_counts,
        top20=top20,
        focus_rows=focus_rows,
        bad_rows=bad_rows,
        bad_buy_or_watch_count=bad_buy_or_watch_count,
        bad_top20_count=0,
        positive_buy_or_watch_count=positive_buy_or_watch_count,
        positive_top20_count=0,
    )


class TradePolicyCalibrationDiagnosticsTests(unittest.TestCase):
    def test_cli_writes_exact_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'calibration_output'
            _build_fixture_db(db_path)
            exit_code = trade_policy_calibration_cli_main(
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
                    'scenario_bad_names.csv',
                    'scenario_focus_tickers.csv',
                    'scenario_signal_counts.csv',
                    'scenario_top20.csv',
                    'threshold_distribution.csv',
                    'trade_policy_calibration_summary.json',
                    'trade_policy_calibration_summary.md',
                ],
            )

    def test_distribution_output_includes_required_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'calibration_output'
            _build_fixture_db(db_path)
            result = build_trade_policy_calibration_report(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            write_trade_policy_calibration_outputs(result=result, out_dir=out_dir)
            with (out_dir / 'threshold_distribution.csv').open('r', encoding='utf-8', newline='') as handle:
                rows = list(csv.DictReader(handle))
            metrics = {row['metric_name'] for row in rows}
            self.assertEqual(metrics, set(THRESHOLD_FIELDS))

    def test_scenarios_include_required_names(self) -> None:
        result = build_trade_policy_calibration_report.__globals__['_scenario_thresholds']()
        self.assertEqual(set(result), {'current', 'drawdown_relaxed', 'balanced_relaxed', 'strict_leader'})

    def test_scenario_counts_match_row_level_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'calibration_output'
            _build_fixture_db(db_path)
            result = build_trade_policy_calibration_report(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            write_trade_policy_calibration_outputs(result=result, out_dir=out_dir)
            with (out_dir / 'scenario_signal_counts.csv').open('r', encoding='utf-8', newline='') as handle:
                counts = list(csv.DictReader(handle))
            by_scenario: dict[str, dict[str, int]] = {}
            for row in counts:
                by_scenario.setdefault(row['scenario_name'], {})[row['trade_signal']] = int(row['count'])
            for scenario in result.scenario_summaries:
                self.assertEqual(by_scenario[scenario.name], {k: int(v) for k, v in scenario.signal_counts.items()})

    def test_zero_buy_current_with_many_drawdown_failures_does_not_recommend_keep_current_policy(self) -> None:
        baseline_rows = tuple(
            _fake_policy_row(
                f'TICKER{i}.OL',
                TradeSignal.AVOID if i < 6 else TradeSignal.REVIEW,
                raw_rank=i + 1,
                policy_pass=False,
                reasons=('deep_drawdown',),
                acceptable_drawdown=False,
            )
            for i in range(10)
        )
        baseline = _fake_policy_result(baseline_rows)
        balanced = _scenario('balanced_relaxed', baseline_rows, bad_buy_or_watch_count=0, positive_buy_or_watch_count=3)
        recommendation = _recommend_scenario(baseline, (balanced,))
        self.assertNotEqual(recommendation, 'keep_current_policy')

    def test_bad_high_risk_buy_promotion_prevents_apply_balanced_threshold_revision(self) -> None:
        baseline = _fake_policy_result((
            _fake_policy_row('AAA.OL', TradeSignal.AVOID, raw_rank=1, policy_pass=False, reasons=('deep_drawdown',), acceptable_drawdown=False),
        ))
        balanced = _scenario(
            'balanced_relaxed',
            (
                _fake_policy_row('NBX.OL', TradeSignal.BUY, raw_rank=1, policy_pass=True, reasons=('strong_trend_profile',)),
            ),
            bad_buy_or_watch_count=1,
            positive_buy_or_watch_count=2,
        )
        self.assertEqual(_recommend_scenario(baseline, (balanced,)), RECOMMEND_REVISE_RANKING)

    def test_balanced_plausible_scenario_can_recommend_apply_balanced_threshold_revision(self) -> None:
        baseline = _fake_policy_result((
            _fake_policy_row('AAA.OL', TradeSignal.AVOID, raw_rank=1, policy_pass=False, reasons=('deep_drawdown',), acceptable_drawdown=False),
            _fake_policy_row('BBB.OL', TradeSignal.AVOID, raw_rank=2, policy_pass=False, reasons=('deep_drawdown',), acceptable_drawdown=False),
            _fake_policy_row('CCC.OL', TradeSignal.AVOID, raw_rank=3, policy_pass=False, reasons=('deep_drawdown',), acceptable_drawdown=False),
            _fake_policy_row('DDD.OL', TradeSignal.AVOID, raw_rank=4, policy_pass=False, reasons=('deep_drawdown',), acceptable_drawdown=False),
            _fake_policy_row('EEE.OL', TradeSignal.AVOID, raw_rank=5, policy_pass=False, reasons=('deep_drawdown',), acceptable_drawdown=False),
        ))
        balanced = _scenario(
            'balanced_relaxed',
            (
                _fake_policy_row('KIT.OL', TradeSignal.BUY, raw_rank=1, policy_pass=True, reasons=('strong_trend_profile',)),
                _fake_policy_row('SUBC.OL', TradeSignal.WATCH, raw_rank=2, policy_pass=True, reasons=('watchlist_candidate',)),
            ),
            bad_buy_or_watch_count=0,
            positive_buy_or_watch_count=2,
        )
        self.assertEqual(_recommend_scenario(baseline, (balanced,)), RECOMMEND_APPLY)

    def test_no_source_policy_thresholds_are_changed(self) -> None:
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

    def test_no_candidate_type_or_ml_score_field_is_produced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'calibration_output'
            _build_fixture_db(db_path)
            result = build_trade_policy_calibration_report(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            write_trade_policy_calibration_outputs(result=result, out_dir=out_dir)
            summary = json.loads((out_dir / 'trade_policy_calibration_summary.json').read_text(encoding='utf-8'))
            with (out_dir / 'scenario_top20.csv').open('r', encoding='utf-8', newline='') as handle:
                header = csv.DictReader(handle).fieldnames or []
            forbidden = {'candidate_type', 'ml_score'}
            self.assertTrue(forbidden.isdisjoint(summary.keys()))
            self.assertTrue(forbidden.isdisjoint(header))

    def test_no_holdings_logic_is_added(self) -> None:
        module_source = Path('src/tradetool/diagnostics/trade_policy_calibration.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('holdings_signal', module_source)
        self.assertNotIn('tradetool.holdings', module_source)

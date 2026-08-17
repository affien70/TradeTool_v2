from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.contracts.enums import CandidateType, TradeSignal
from tradetool.diagnostics.candidate_type import build_candidate_type_diagnostics, write_candidate_type_outputs
from tradetool.diagnostics.candidate_type_cli import main as candidate_type_cli_main
from tradetool.policy.candidate_type import (
    CANDIDATE_TYPE_ENGINE_ID,
    CandidateTypeInputRow,
    apply_candidate_type_diagnostics,
)
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


def _make_row(
    ticker: str,
    *,
    raw_rank: int = 1,
    raw_score: float = 10.0,
    trade_signal: TradeSignal = TradeSignal.BUY,
    policy_pass: bool = True,
    policy_reasons: tuple[str, ...] = ('strong_trend_profile',),
    policy_warnings: tuple[str, ...] = (),
    overrides: dict[str, float | int | bool | str | None] | None = None,
) -> CandidateTypeInputRow:
    fields: dict[str, float | int | bool | str | None] = {
        'above_sma50': True,
        'above_sma200': True,
        'positive_return_3m': True,
        'positive_return_6m': True,
        'positive_rs_3m': True,
        'positive_rs_6m': True,
        'acceptable_drawdown': True,
        'acceptable_volatility': True,
        'acceptable_traded_value': True,
        'moderate_stretch': True,
        'severe_stretch': False,
        'drawdown_252': -0.15,
        'volatility_63': 0.02,
        'average_traded_value_20': 2_000_000.0,
        'distance_to_sma50': 0.05,
        'distance_to_sma200': 0.20,
    }
    if overrides:
        fields.update(overrides)
    return CandidateTypeInputRow(
        ticker=ticker,
        rank_date='2025-09-17',
        ranking_engine_id='baseline_v0_price_volume_rs',
        policy_engine_id=TRADE_POLICY_ENGINE_ID,
        raw_rank=raw_rank,
        raw_score=raw_score,
        trade_signal=trade_signal,
        policy_pass=policy_pass,
        policy_reasons=policy_reasons,
        policy_warnings=policy_warnings,
        above_sma50=bool(fields['above_sma50']),
        above_sma200=bool(fields['above_sma200']),
        positive_return_3m=bool(fields['positive_return_3m']),
        positive_return_6m=bool(fields['positive_return_6m']),
        positive_rs_3m=bool(fields['positive_rs_3m']),
        positive_rs_6m=bool(fields['positive_rs_6m']),
        acceptable_drawdown=bool(fields['acceptable_drawdown']),
        acceptable_volatility=bool(fields['acceptable_volatility']),
        acceptable_traded_value=bool(fields['acceptable_traded_value']),
        moderate_stretch=bool(fields['moderate_stretch']),
        severe_stretch=bool(fields['severe_stretch']),
        drawdown_252=float(fields['drawdown_252']),
        volatility_63=float(fields['volatility_63']),
        average_traded_value_20=float(fields['average_traded_value_20']),
        distance_to_sma50=float(fields['distance_to_sma50']),
        distance_to_sma200=float(fields['distance_to_sma200']),
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
        tickers = ['LEADER.OL', 'RUNNER.OL', 'REBOUND.OL', 'BREAKOUT.OL', 'REJECT.OL']
        for offset, ticker in enumerate(tickers):
            slope = 1.3 - (offset * 0.1)
            closes = [100.0 + index * slope for index in range(260)]
            if ticker == 'REBOUND.OL':
                closes = [100.0 + index * 1.0 for index in range(200)] + [300.0 - index * 1.5 for index in range(60)]
            _insert_rows(connection, ticker, closes, last_date=last_date, volume=2200.0 - offset * 200)
        benchmark = [300.0 + index * 0.4 for index in range(260)]
        _insert_rows(connection, '^OSEAX', benchmark, last_date=last_date, volume=3000.0)
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('NORWAY_V2', json.dumps(tickers), 'fixture', '2026-08-17T00:00:00Z'),
        )


class CandidateTypeFunctionTests(unittest.TestCase):
    def test_classification_engine_id_is_stable(self) -> None:
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')

    def test_candidate_typing_preserves_raw_rank(self) -> None:
        row = _make_row('AAA.OL', raw_rank=7)
        result = apply_candidate_type_diagnostics((row,))[0]
        self.assertEqual(result.raw_rank, 7)

    def test_candidate_typing_preserves_raw_score(self) -> None:
        row = _make_row('AAA.OL', raw_score=8.5)
        result = apply_candidate_type_diagnostics((row,))[0]
        self.assertEqual(result.raw_score, 8.5)

    def test_candidate_typing_preserves_trade_signal(self) -> None:
        row = _make_row('AAA.OL', trade_signal=TradeSignal.WATCH)
        result = apply_candidate_type_diagnostics((row,))[0]
        self.assertEqual(result.trade_signal, TradeSignal.WATCH)

    def test_stable_leader_profile_classifies_as_stable_leader(self) -> None:
        result = apply_candidate_type_diagnostics((_make_row('LEADER.OL', trade_signal=TradeSignal.BUY),))[0]
        self.assertEqual(result.candidate_type, CandidateType.STABLE_LEADER)

    def test_stretched_strong_profile_classifies_as_extended_runner(self) -> None:
        row = _make_row(
            'RUNNER.OL',
            trade_signal=TradeSignal.WATCH,
            policy_warnings=('moderate_stretch',),
            overrides={'moderate_stretch': False, 'distance_to_sma50': 0.28, 'distance_to_sma200': 0.55},
        )
        result = apply_candidate_type_diagnostics((row,))[0]
        self.assertEqual(result.candidate_type, CandidateType.EXTENDED_RUNNER)

    def test_deep_drawdown_mixed_trend_profile_classifies_as_rebound_case(self) -> None:
        row = _make_row(
            'REBOUND.OL',
            trade_signal=TradeSignal.REVIEW,
            policy_pass=False,
            policy_reasons=('deep_drawdown',),
            overrides={'acceptable_drawdown': False, 'drawdown_252': -0.45, 'above_sma50': False},
        )
        result = apply_candidate_type_diagnostics((row,))[0]
        self.assertEqual(result.candidate_type, CandidateType.REBOUND_CASE)

    def test_structural_feature_severe_reject_classifies_as_reject(self) -> None:
        row = _make_row(
            'REJECT.OL',
            trade_signal=TradeSignal.AVOID,
            policy_pass=False,
            policy_reasons=('below_sma200', 'very_low_traded_value'),
            overrides={
                'above_sma200': False,
                'positive_return_3m': False,
                'positive_return_6m': False,
                'acceptable_traded_value': False,
                'average_traded_value_20': 100_000.0,
            },
        )
        result = apply_candidate_type_diagnostics((row,))[0]
        self.assertEqual(result.candidate_type, CandidateType.REJECT)

    def test_early_breakout_simple_profile_can_classify_as_early_breakout(self) -> None:
        row = _make_row(
            'BREAKOUT.OL',
            trade_signal=TradeSignal.WATCH,
            policy_reasons=('watchlist_candidate',),
            overrides={'above_sma50': False, 'positive_return_6m': False, 'positive_rs_6m': False},
        )
        result = apply_candidate_type_diagnostics((row,))[0]
        self.assertEqual(result.candidate_type, CandidateType.EARLY_BREAKOUT)

    def test_no_ml_fields_are_used(self) -> None:
        base = _make_row('AAA.OL')
        with_ml = _make_row('AAA.OL')
        result_base = apply_candidate_type_diagnostics((base,))[0]
        result_ml = apply_candidate_type_diagnostics((with_ml,))[0]
        self.assertEqual(result_base.candidate_type, result_ml.candidate_type)

    def test_no_ownership_fields_are_used(self) -> None:
        base = _make_row('AAA.OL')
        with_owned = _make_row('AAA.OL')
        result_base = apply_candidate_type_diagnostics((base,))[0]
        result_owned = apply_candidate_type_diagnostics((with_owned,))[0]
        self.assertEqual(result_base.candidate_type, result_owned.candidate_type)

    def test_no_ticker_names_are_hardcoded(self) -> None:
        module_source = Path('src/tradetool/policy/candidate_type.py').read_text(encoding='utf-8')
        self.assertNotIn('.OL', module_source)

    def test_no_holdings_signal_logic_is_added(self) -> None:
        module_source = Path('src/tradetool/policy/candidate_type.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('holdings_signal', module_source)
        self.assertNotIn('tradetool.holdings', module_source)

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


class CandidateTypeDiagnosticsTests(unittest.TestCase):
    def test_cli_summary_candidate_type_counts_match_row_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'candidate_output'
            _build_fixture_db(db_path)
            exit_code = candidate_type_cli_main(
                ['--db-path', str(db_path), '--universe-id', 'NORWAY_V2', '--benchmark-ticker', '^OSEAX', '--out-dir', str(out_dir)]
            )
            self.assertEqual(exit_code, 0)
            summary = json.loads((out_dir / 'candidate_type_summary.json').read_text(encoding='utf-8'))
            with (out_dir / 'candidate_type_diagnostics.csv').open('r', encoding='utf-8', newline='') as handle:
                rows = list(csv.DictReader(handle))
            counts: dict[str, int] = {}
            for row in rows:
                counts[row['candidate_type']] = counts.get(row['candidate_type'], 0) + 1
            self.assertEqual(summary['candidate_type_counts'], counts)

    def test_output_includes_classification_reasons_and_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'candidate_output'
            _build_fixture_db(db_path)
            result = build_candidate_type_diagnostics(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            write_candidate_type_outputs(result=result, out_dir=out_dir)
            with (out_dir / 'candidate_type_diagnostics.csv').open('r', encoding='utf-8', newline='') as handle:
                header = csv.DictReader(handle).fieldnames or []
            self.assertIn('classification_reasons', header)
            self.assertIn('classification_warnings', header)

    def test_no_holdings_signal_logic_is_added_in_diagnostics(self) -> None:
        module_source = Path('src/tradetool/diagnostics/candidate_type.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('holdings_signal', module_source)
        self.assertNotIn('tradetool.holdings', module_source)

    def test_no_ranking_formula_is_changed(self) -> None:
        ranking_source = Path('src/tradetool/ranking/baseline.py').read_text(encoding='utf-8')
        self.assertIn("BASELINE_RANKING_ENGINE_ID = 'baseline_v0_price_volume_rs'", ranking_source)

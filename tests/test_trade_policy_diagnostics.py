from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.contracts.enums import TradeSignal
from tradetool.diagnostics.trade_policy import build_trade_policy_diagnostics, write_trade_policy_outputs
from tradetool.diagnostics.trade_policy_cli import main as trade_policy_cli_main
from tradetool.policy.trade_policy import TRADE_POLICY_ENGINE_ID, TradePolicyInputRow, apply_trade_policy_diagnostics


def _make_row(
    ticker: str,
    *,
    raw_rank: int = 1,
    raw_score: float = 10.0,
    overrides: dict[str, float | int | bool | str | None] | None = None,
) -> TradePolicyInputRow:
    fields: dict[str, float | int | bool | str | None] = {
        'above_sma50': True,
        'above_sma200': True,
        'return_3m': 0.10,
        'return_6m': 0.15,
        'relative_strength_3m': 0.05,
        'relative_strength_6m': 0.10,
        'drawdown_252': -0.15,
        'volatility_63': 0.02,
        'average_traded_value_20': 2_000_000.0,
        'distance_to_sma50': 0.05,
        'distance_to_sma200': 0.20,
        'latest_price_date': '2025-09-17',
    }
    if overrides:
        fields.update(overrides)
    return TradePolicyInputRow(
        ticker=ticker,
        rank_date='2025-09-17',
        ranking_engine_id='baseline_v0_price_volume_rs',
        raw_rank=raw_rank,
        raw_score=raw_score,
        input_fields=fields,
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
        tickers = ['LEADER.OL', 'STRETCH.OL', 'WEAKRS.OL', 'DRAWDOWN.OL', 'LOWVOL.OL']
        for offset, ticker in enumerate(tickers):
            slope = 1.3 - (offset * 0.08)
            closes = [100.0 + index * slope for index in range(260)]
            if ticker == 'DRAWDOWN.OL':
                closes = [100.0 + index * 1.1 for index in range(200)] + [320.0 - index * 1.6 for index in range(60)]
            _insert_rows(connection, ticker, closes, last_date=last_date, volume=2500.0 - offset * 400)
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


class TradePolicyFunctionTests(unittest.TestCase):
    def test_policy_engine_id_is_balanced_v1(self) -> None:
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')

    def test_policy_does_not_change_raw_rank(self) -> None:
        row = _make_row('AAA.OL', raw_rank=7)
        result = apply_trade_policy_diagnostics((row,))[0]
        self.assertEqual(result.raw_rank, 7)

    def test_policy_does_not_change_raw_score(self) -> None:
        row = _make_row('AAA.OL', raw_score=8.75)
        result = apply_trade_policy_diagnostics((row,))[0]
        self.assertEqual(result.raw_score, 8.75)

    def test_policy_does_not_use_ml_fields(self) -> None:
        base = _make_row('AAA.OL')
        with_ml = _make_row('AAA.OL', overrides={'ml_score': 999.0})
        self.assertEqual(apply_trade_policy_diagnostics((base,))[0].trade_signal, apply_trade_policy_diagnostics((with_ml,))[0].trade_signal)

    def test_policy_does_not_use_ownership_fields(self) -> None:
        base = _make_row('AAA.OL')
        owned = _make_row('AAA.OL', overrides={'owned': True})
        self.assertEqual(apply_trade_policy_diagnostics((base,))[0].trade_signal, apply_trade_policy_diagnostics((owned,))[0].trade_signal)

    def test_policy_does_not_hardcode_tickers(self) -> None:
        module_source = Path('src/tradetool/policy/trade_policy.py').read_text(encoding='utf-8')
        self.assertNotIn('.OL', module_source)

    def test_clear_leader_profile_can_be_buy(self) -> None:
        result = apply_trade_policy_diagnostics((_make_row('LEADER.OL', raw_rank=5),))[0]
        self.assertEqual(result.trade_signal, TradeSignal.BUY)

    def test_stretched_strong_profile_is_capped_below_buy(self) -> None:
        row = _make_row('STRETCH.OL', raw_rank=5, overrides={'distance_to_sma50': 0.28, 'distance_to_sma200': 0.55})
        result = apply_trade_policy_diagnostics((row,))[0]
        self.assertIn(result.trade_signal, {TradeSignal.WATCH, TradeSignal.REVIEW})

    def test_weak_rs_momentum_is_not_buy(self) -> None:
        row = _make_row('WEAKRS.OL', overrides={'relative_strength_3m': -0.01, 'relative_strength_6m': -0.02})
        result = apply_trade_policy_diagnostics((row,))[0]
        self.assertNotEqual(result.trade_signal, TradeSignal.BUY)

    def test_deep_drawdown_alone_does_not_automatically_force_avoid_if_other_gates_are_strong(self) -> None:
        row = _make_row('DRAWDOWN.OL', overrides={'drawdown_252': -0.35})
        result = apply_trade_policy_diagnostics((row,))[0]
        self.assertNotEqual(result.trade_signal, TradeSignal.AVOID)

    def test_bad_high_risk_style_weak_profile_is_not_buy(self) -> None:
        row = _make_row('DRAWDOWN.OL', overrides={'drawdown_252': -0.45, 'relative_strength_3m': -0.05, 'return_3m': -0.03})
        result = apply_trade_policy_diagnostics((row,))[0]
        self.assertNotEqual(result.trade_signal, TradeSignal.BUY)

    def test_low_traded_value_is_not_buy(self) -> None:
        row = _make_row('LOWVOL.OL', overrides={'average_traded_value_20': 150_000.0})
        result = apply_trade_policy_diagnostics((row,))[0]
        self.assertNotEqual(result.trade_signal, TradeSignal.BUY)

    def test_no_streamlit_import_inside_policy_module(self) -> None:
        module_source = Path('src/tradetool/policy/trade_policy.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('streamlit', module_source)

    def test_no_holdings_signal_logic_is_added(self) -> None:
        module_source = Path('src/tradetool/policy/trade_policy.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('holdings_signal', module_source)
        self.assertNotIn('tradetool.holdings', module_source)


class TradePolicyDiagnosticsTests(unittest.TestCase):
    def test_cli_writes_only_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'policy_output'
            _build_fixture_db(db_path)
            exit_code = trade_policy_cli_main(
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
                ['trade_policy_diagnostics.csv', 'trade_policy_summary.json', 'trade_policy_summary.md'],
            )

    def test_summary_signal_counts_match_per_row_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'policy_output'
            _build_fixture_db(db_path)
            result = build_trade_policy_diagnostics(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            write_trade_policy_outputs(result=result, out_dir=out_dir)
            summary = json.loads((out_dir / 'trade_policy_summary.json').read_text(encoding='utf-8'))
            with (out_dir / 'trade_policy_diagnostics.csv').open('r', encoding='utf-8', newline='') as handle:
                rows = list(csv.DictReader(handle))
            counts: dict[str, int] = {}
            for row in rows:
                counts[row['trade_signal']] = counts.get(row['trade_signal'], 0) + 1
            self.assertEqual(summary['signal_counts'], counts)

    def test_output_includes_policy_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'policy_output'
            _build_fixture_db(db_path)
            result = build_trade_policy_diagnostics(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            write_trade_policy_outputs(result=result, out_dir=out_dir)
            with (out_dir / 'trade_policy_diagnostics.csv').open('r', encoding='utf-8', newline='') as handle:
                header = csv.DictReader(handle).fieldnames or []
            self.assertIn('policy_reasons', header)

    def test_no_candidate_type_or_ml_score_is_produced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'policy_output'
            _build_fixture_db(db_path)
            result = build_trade_policy_diagnostics(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            write_trade_policy_outputs(result=result, out_dir=out_dir)
            summary = json.loads((out_dir / 'trade_policy_summary.json').read_text(encoding='utf-8'))
            with (out_dir / 'trade_policy_diagnostics.csv').open('r', encoding='utf-8', newline='') as handle:
                header = csv.DictReader(handle).fieldnames or []
            forbidden = {'candidate_type', 'ml_score'}
            self.assertTrue(forbidden.isdisjoint(summary.keys()))
            self.assertTrue(forbidden.isdisjoint(header))

    def test_trade_policy_result_preserves_engine_and_row_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_fixture_db(db_path)
            result = build_trade_policy_diagnostics(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            self.assertEqual(result.policy_engine_id, TRADE_POLICY_ENGINE_ID)
            self.assertEqual(result.ranking.ranked_count, len(result.rows))

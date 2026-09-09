from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.data.market_data_schema import MarketDataRow, initialize_market_data_schema, write_market_data_rows_for_test
from tradetool.diagnostics.holdout_forward_returns import (
    DECISION_BLOCKED_HISTORY,
    DECISION_CONTINUE,
    EXPECTED_REPORT_FILES,
    build_holdout_forward_returns,
    recommend_next_action,
    write_holdout_forward_return_outputs,
)
from tradetool.diagnostics.holdout_forward_returns_cli import build_argument_parser
from tradetool.diagnostics.holdout_forward_returns_cli import main as holdout_forward_returns_cli_main
from tradetool.policy.candidate_type import CANDIDATE_TYPE_ENGINE_ID
from tradetool.policy.trade_policy import MAX_BUY_RAW_RANK, MIN_ACCEPTABLE_TRADED_VALUE, TRADE_POLICY_ENGINE_ID
from tradetool.ranking.baseline import BASELINE_RANKING_ENGINE_ID


def _market_row(
    *,
    ticker: str,
    price_date: date,
    close: float,
    volume: float = 2_000_000.0,
    data_source: str = 'yahoo',
) -> MarketDataRow:
    return MarketDataRow(
        ticker=ticker,
        price_date=price_date.isoformat(),
        raw_open=close,
        raw_high=close + 1.0,
        raw_low=max(0.01, close - 1.0),
        raw_close=close,
        adjusted_close=close,
        volume=volume,
        data_source=data_source,
        created_at_utc='2026-09-09T00:00:00Z',
        updated_at_utc='2026-09-09T00:00:00Z',
    )


def _seed_db(path: Path, *, benchmark_days_after: int = 70, ticker_days_after: int = 70) -> None:
    initialize_market_data_schema(path)
    start = date(2025, 1, 1)
    as_of = date(2025, 9, 27)
    rows: list[MarketDataRow] = []
    for index in range(270):
        day = start + timedelta(days=index)
        rows.append(_market_row(ticker='DNB.OL', price_date=day, close=100.0 + index * 0.7))
        rows.append(_market_row(ticker='NONG.OL', price_date=day, close=90.0 + index * 0.6))
        rows.append(_market_row(ticker='OSEBX.OL', price_date=day, close=300.0 + index * 0.2))
    for offset in range(1, ticker_days_after + 1):
        day = as_of + timedelta(days=offset)
        rows.append(_market_row(ticker='DNB.OL', price_date=day, close=300.0 + offset * 2.0))
        rows.append(_market_row(ticker='NONG.OL', price_date=day, close=250.0 + offset * 1.0))
    for offset in range(1, benchmark_days_after + 1):
        day = as_of + timedelta(days=offset)
        rows.append(_market_row(ticker='OSEBX.OL', price_date=day, close=400.0 + offset * 0.5))
    future = as_of + timedelta(days=80)
    rows.append(_market_row(ticker='DNB.OL', price_date=future, close=999.0))
    write_market_data_rows_for_test(db_path=path, rows=rows, allow_test_db_write=True)


def _seed_universe(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE universe_cache (universe_key TEXT PRIMARY KEY, tickers_json TEXT, source_label TEXT, updated_at TEXT)')
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('NORWAY_V2', json.dumps(['DNB.OL', 'NONG.OL', 'MISSING.OL']), 'unit', '2026-09-09T00:00:00Z'),
        )


class HoldoutForwardReturnsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path('/tmp/tradetool_holdout_forward_unit.sqlite')
        self.universe_path = Path('/tmp/tradetool_holdout_forward_universe.sqlite')
        self.out_dir = Path('/tmp/tradetool_holdout_forward_output')
        self._cleanup()

    def tearDown(self) -> None:
        self._cleanup()

    def test_forward_returns_use_rows_after_as_of_and_exits_are_correct(self) -> None:
        _seed_db(self.db_path)
        _seed_universe(self.universe_path)
        result = build_holdout_forward_returns(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            windows=(20, 60),
            universe_db_path=self.universe_path,
        )
        row = next(row for row in result.candidate_rows if row['ticker'] == 'DNB.OL')
        self.assertGreater(date.fromisoformat(str(row['entry_date'])), date(2025, 9, 27))
        self.assertEqual(row['20d_exit_date'], (date(2025, 9, 27) + timedelta(days=21)).isoformat())
        self.assertEqual(row['60d_exit_date'], (date(2025, 9, 27) + timedelta(days=61)).isoformat())
        self.assertTrue(row['20d_complete'])
        self.assertTrue(row['60d_complete'])

    def test_benchmark_returns_align_to_selected_window_and_excess_is_difference(self) -> None:
        _seed_db(self.db_path)
        _seed_universe(self.universe_path)
        result = build_holdout_forward_returns(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            windows=(20,),
            universe_db_path=self.universe_path,
        )
        row = next(row for row in result.candidate_rows if row['ticker'] == 'DNB.OL')
        self.assertEqual(row['20d_benchmark_entry_date'], row['entry_date'])
        self.assertEqual(row['20d_benchmark_exit_date'], row['20d_exit_date'])
        self.assertAlmostEqual(float(row['20d_excess_return']), float(row['20d_return']) - float(row['20d_benchmark_return']))

    def test_missing_ticker_exit_and_benchmark_exit_are_reported(self) -> None:
        _seed_db(self.db_path, benchmark_days_after=30, ticker_days_after=30)
        _seed_universe(self.universe_path)
        result = build_holdout_forward_returns(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            windows=(20, 60),
            universe_db_path=self.universe_path,
        )
        reasons = {row['missing_reason'] for row in result.missing_exit_rows}
        self.assertIn('missing_ticker_exit', reasons)

        self._cleanup()
        _seed_db(self.db_path, benchmark_days_after=30, ticker_days_after=70)
        _seed_universe(self.universe_path)
        result = build_holdout_forward_returns(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            windows=(60,),
            universe_db_path=self.universe_path,
        )
        reasons = {row['missing_reason'] for row in result.missing_exit_rows}
        self.assertIn('missing_benchmark_exit', reasons)

    def test_aggregate_group_summaries_are_calculated(self) -> None:
        _seed_db(self.db_path)
        _seed_universe(self.universe_path)
        result = build_holdout_forward_returns(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            windows=(20, 60),
            universe_db_path=self.universe_path,
        )
        self.assertTrue(result.signal_summary_rows)
        self.assertTrue(result.candidate_type_summary_rows)
        self.assertTrue(result.topn_summary_rows)
        self.assertTrue(all('mean_excess_return' in row for row in result.topn_summary_rows))

    def test_forward_returns_do_not_alter_raw_rank_signal_or_type(self) -> None:
        _seed_db(self.db_path)
        _seed_universe(self.universe_path)
        result = build_holdout_forward_returns(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            windows=(20,),
            universe_db_path=self.universe_path,
        )
        snapshot_by_ticker = {row['ticker']: row for row in result.snapshot.ranked_rows}
        for row in result.candidate_rows:
            original = snapshot_by_ticker[row['ticker']]
            self.assertEqual(row['raw_rank'], original['raw_rank'])
            self.assertEqual(row['trade_signal'], original['trade_signal'])
            self.assertEqual(row['candidate_type'], original['candidate_type'])

    def test_no_forward_ml_or_holdings_fields_are_output(self) -> None:
        _seed_db(self.db_path)
        _seed_universe(self.universe_path)
        result = build_holdout_forward_returns(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            windows=(20,),
            universe_db_path=self.universe_path,
        )
        for row in result.candidate_rows:
            lowered = {key.lower() for key in row}
            self.assertFalse(any('ml_score' in key for key in lowered))
            self.assertFalse(any('holdings' in key for key in lowered))
        self.assertFalse(result.to_summary_dict()['leakage_controls']['ml_score_calculated'])
        self.assertFalse(result.to_summary_dict()['leakage_controls']['holdings_adjustment_applied'])

    def test_output_files_are_written(self) -> None:
        _seed_db(self.db_path)
        _seed_universe(self.universe_path)
        result = build_holdout_forward_returns(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            windows=(20, 60),
            universe_db_path=self.universe_path,
        )
        write_holdout_forward_return_outputs(result=result, out_dir=self.out_dir)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))
        summary = json.loads((self.out_dir / 'holdout_forward_returns_summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['decision_recommendation'], DECISION_CONTINUE)

    def test_cli_writes_outputs_and_requires_explicit_db_path(self) -> None:
        _seed_db(self.db_path)
        _seed_universe(self.universe_path)
        parser = build_argument_parser()
        db_action = next(action for action in parser._actions if '--db-path' in action.option_strings)
        self.assertTrue(db_action.required)
        self.assertIsNone(db_action.default)
        exit_code = holdout_forward_returns_cli_main([
            '--db-path', str(self.db_path),
            '--universe-id', 'NORWAY_V2',
            '--benchmark-ticker', 'OSEBX.OL',
            '--as-of-date', '2025-09-27',
            '--data-source', 'yahoo',
            '--windows', '20', '60',
            '--universe-db-path', str(self.universe_path),
            '--out-dir', str(self.out_dir),
        ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(sorted(path.name for path in self.out_dir.iterdir()), sorted(EXPECTED_REPORT_FILES))

    def test_missing_benchmark_history_blocks_forward_return_decision(self) -> None:
        _seed_db(self.db_path, benchmark_days_after=0)
        _seed_universe(self.universe_path)
        result = build_holdout_forward_returns(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='MISSINGBENCH.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            windows=(20,),
            universe_db_path=self.universe_path,
        )
        self.assertEqual(result.decision_recommendation, DECISION_BLOCKED_HISTORY)

    def test_no_db_writes_occur(self) -> None:
        _seed_db(self.db_path)
        _seed_universe(self.universe_path)
        before = self.db_path.stat().st_mtime_ns
        build_holdout_forward_returns(
            db_path=self.db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 27),
            data_source='yahoo',
            windows=(20,),
            universe_db_path=self.universe_path,
        )
        self.assertEqual(before, self.db_path.stat().st_mtime_ns)

    def test_ranking_feature_policy_candidate_type_constants_are_unchanged(self) -> None:
        self.assertEqual(BASELINE_RANKING_ENGINE_ID, 'baseline_v0_price_volume_rs')
        self.assertEqual(TRADE_POLICY_ENGINE_ID, 'trade_policy_v1_balanced_diagnostic')
        self.assertEqual(CANDIDATE_TYPE_ENGINE_ID, 'candidate_type_v0_diagnostic')
        self.assertEqual(MAX_BUY_RAW_RANK, 20)
        self.assertEqual(MIN_ACCEPTABLE_TRADED_VALUE, 750_000.0)

    def _cleanup(self) -> None:
        for path in (self.db_path, self.universe_path):
            if path.exists():
                path.unlink()
        if self.out_dir.exists():
            for child in self.out_dir.iterdir():
                child.unlink()
            self.out_dir.rmdir()


if __name__ == '__main__':
    unittest.main()

from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.diagnostics.baseline_ranking import build_baseline_ranking_diagnostics
from tradetool.diagnostics.baseline_ranking_cli import main as baseline_ranking_cli_main
from tradetool.ranking import BASELINE_RANKING_ENGINE_ID, BaselineRankingInput, build_baseline_ranking


def _make_features(**overrides: float | int | bool | str | None) -> dict[str, float | int | bool | str | None]:
    features: dict[str, float | int | bool | str | None] = {
        'relative_strength_3m': 0.10,
        'relative_strength_6m': 0.20,
        'return_3m': 0.08,
        'return_6m': 0.12,
        'above_sma200': True,
        'above_sma50': True,
        'drawdown_252': -0.15,
        'volatility_63': 0.02,
        'average_traded_value_20': 2_000_000.0,
        'distance_to_sma50': 0.05,
        'distance_to_sma200': 0.20,
    }
    features.update(overrides)
    return features


def _insert_rows(connection: sqlite3.Connection, ticker: str, closes: list[float], *, last_date: date, volume: float = 100.0) -> None:
    rows = []
    start = last_date - timedelta(days=len(closes) - 1)
    for index, close_value in enumerate(closes):
        current_date = start + timedelta(days=index)
        rows.append((ticker, current_date.isoformat(), close_value, close_value + 1.0, close_value - 1.0, close_value, volume + index))
    connection.executemany('INSERT INTO price_history VALUES (?, ?, ?, ?, ?, ?, ?)', rows)


def _build_ranking_fixture_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            'CREATE TABLE price_history (ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)'
        )
        connection.execute(
            'CREATE TABLE universe_cache (universe_key TEXT PRIMARY KEY, tickers_json TEXT, source_label TEXT, updated_at TEXT)'
        )
        last_date = date(2025, 1, 1) + timedelta(days=259)
        strong = [100.0 + index * 1.2 for index in range(260)]
        medium = [100.0 + index * 0.8 for index in range(255)]
        short = [50.0 + index for index in range(200)]
        benchmark = [300.0 + index * 0.6 for index in range(260)]
        _insert_rows(connection, 'STRONG.OL', strong, last_date=last_date, volume=2000.0)
        _insert_rows(connection, 'MEDIUM.OL', medium, last_date=last_date, volume=1200.0)
        _insert_rows(connection, 'SHORT.OL', short, last_date=last_date, volume=400.0)
        _insert_rows(connection, '^OSEAX', benchmark, last_date=last_date, volume=2500.0)
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            (
                'NORWAY_V2',
                json.dumps(['STRONG.OL', 'MEDIUM.OL', 'SHORT.OL']),
                'fixture',
                '2026-08-16T00:00:00Z',
            ),
        )


class BaselineRankingTests(unittest.TestCase):
    def test_ranks_are_one_based_and_sorting_is_deterministic(self) -> None:
        inputs = [
            BaselineRankingInput(ticker='BBB.OL', rank_date=date(2026, 8, 16), features=_make_features(relative_strength_6m=0.3, relative_strength_3m=0.2)),
            BaselineRankingInput(ticker='AAA.OL', rank_date=date(2026, 8, 16), features=_make_features(relative_strength_6m=0.3, relative_strength_3m=0.2)),
            BaselineRankingInput(ticker='CCC.OL', rank_date=date(2026, 8, 16), features=_make_features(relative_strength_6m=0.1, relative_strength_3m=0.1)),
        ]
        ranked = build_baseline_ranking(inputs)
        self.assertEqual([row.ranked_candidate.raw_rank for row in ranked], [1, 2, 3])
        self.assertEqual([row.ranked_candidate.ticker for row in ranked[:2]], ['AAA.OL', 'BBB.OL'])

    def test_higher_rs_and_momentum_improve_score(self) -> None:
        low = build_baseline_ranking(
            [BaselineRankingInput(ticker='LOW.OL', rank_date=date(2026, 8, 16), features=_make_features(relative_strength_3m=-0.1, relative_strength_6m=-0.1, return_3m=-0.05, return_6m=-0.05))]
        )[0]
        high = build_baseline_ranking(
            [BaselineRankingInput(ticker='HIGH.OL', rank_date=date(2026, 8, 16), features=_make_features(relative_strength_3m=0.2, relative_strength_6m=0.3, return_3m=0.1, return_6m=0.2))]
        )[0]
        self.assertGreater(high.ranked_candidate.raw_score, low.ranked_candidate.raw_score)

    def test_excessive_drawdown_or_stretch_reduce_score(self) -> None:
        clean = build_baseline_ranking(
            [BaselineRankingInput(ticker='CLEAN.OL', rank_date=date(2026, 8, 16), features=_make_features())]
        )[0]
        stressed = build_baseline_ranking(
            [BaselineRankingInput(ticker='STRETCH.OL', rank_date=date(2026, 8, 16), features=_make_features(drawdown_252=-0.5, distance_to_sma50=0.4, distance_to_sma200=0.8))]
        )[0]
        self.assertLess(stressed.ranked_candidate.raw_score, clean.ranked_candidate.raw_score)

    def test_score_does_not_use_ml_or_ownership_fields(self) -> None:
        clean = build_baseline_ranking(
            [BaselineRankingInput(ticker='BASE.OL', rank_date=date(2026, 8, 16), features=_make_features(ml_score=999.0, owned=True))]
        )[0]
        same = build_baseline_ranking(
            [BaselineRankingInput(ticker='BASE.OL', rank_date=date(2026, 8, 16), features=_make_features())]
        )[0]
        self.assertEqual(clean.ranked_candidate.raw_score, same.ranked_candidate.raw_score)


class BaselineRankingDiagnosticsTests(unittest.TestCase):
    def test_ranking_only_includes_feature_complete_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_ranking_fixture_db(db_path)
            result = build_baseline_ranking_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
            )
            self.assertEqual(result.feature_complete_count, 2)
            self.assertEqual(result.ranked_count, 2)
            self.assertEqual(sorted(row.ticker for row in result.rows), ['MEDIUM.OL', 'STRONG.OL'])

    def test_structurally_rejected_rows_are_counted_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_ranking_fixture_db(db_path)
            result = build_baseline_ranking_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
            )
            self.assertEqual(result.input_universe_count, 3)
            self.assertEqual(result.structural_eligible_count, 2)
            self.assertEqual(result.structural_rejected_count, 1)

    def test_cli_writes_only_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'ranking_output'
            _build_ranking_fixture_db(db_path)
            exit_code = baseline_ranking_cli_main(
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
                ['baseline_ranking.csv', 'baseline_ranking_summary.json', 'baseline_ranking_summary.md'],
            )

    def test_summary_counts_match_ranking_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_ranking_fixture_db(db_path)
            result = build_baseline_ranking_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
            )
            self.assertEqual(result.ranked_count, len(result.rows))

    def test_no_trade_signal_candidate_type_or_ml_score_is_produced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            out_dir = Path(temp_dir) / 'ranking_output'
            _build_ranking_fixture_db(db_path)
            baseline_ranking_cli_main(
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
            summary = json.loads((out_dir / 'baseline_ranking_summary.json').read_text(encoding='utf-8'))
            with (out_dir / 'baseline_ranking.csv').open('r', encoding='utf-8', newline='') as handle:
                header = csv.DictReader(handle).fieldnames or []
            forbidden_fields = {'ml_score', 'trade_signal', 'candidate_type'}
            self.assertTrue(forbidden_fields.isdisjoint(summary.keys()))
            self.assertTrue(forbidden_fields.isdisjoint(header))

    def test_ranking_engine_id_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_ranking_fixture_db(db_path)
            result = build_baseline_ranking_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
            )
            self.assertEqual(result.ranking_engine_id, BASELINE_RANKING_ENGINE_ID)

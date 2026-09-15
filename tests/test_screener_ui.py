from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.data.market_data_schema import MarketDataRow, initialize_market_data_schema, write_market_data_rows_for_test
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
from tradetool.ui.screener import (
    CHART_PERIOD_ROW_COUNTS,
    DEFAULT_CHART_PERIOD_LABEL,
    PRICE_TABLE_LEGACY,
    PRICE_TABLE_V2,
    build_incumbent_screener_ui_result,
    build_minimal_screener_result,
    build_price_chart_spec,
    build_relative_strength_chart_spec,
    build_selected_ticker_chart_detail,
    build_selected_ticker_detail,
    incumbent_candidate_explanation,
    incumbent_candidate_detail_rows,
    incumbent_screener_summary_rows,
    selected_incumbent_candidate,
    incumbent_screener_table_rows,
    incumbent_screener_ticker_options,
    resolve_incumbent_selected_ticker,
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


def _sample_v2_row(
    *,
    ticker: str,
    price_date: str,
    raw_close: float,
    adjusted_close: float,
    volume: float = 1000.0,
    data_source: str = 'yahoo',
) -> MarketDataRow:
    return MarketDataRow(
        ticker=ticker,
        price_date=price_date,
        raw_open=raw_close,
        raw_high=raw_close + 1.0,
        raw_low=raw_close - 1.0,
        raw_close=raw_close,
        adjusted_close=adjusted_close,
        volume=volume,
        data_source=data_source,
        created_at_utc='2026-09-09T00:00:00Z',
        updated_at_utc='2026-09-09T00:00:00Z',
    )


def _build_fixture_v2_db(path: Path) -> None:
    initialize_market_data_schema(path)
    rows: list[MarketDataRow] = []
    start = date(2025, 1, 1)
    for index in range(260):
        day = (start + timedelta(days=index)).isoformat()
        rows.append(_sample_v2_row(ticker='CAMBI.OL', price_date=day, raw_close=100.0 + index, adjusted_close=80.0 + index, volume=2000.0 + index))
        rows.append(_sample_v2_row(ticker='SNTIA.OL', price_date=day, raw_close=120.0 + index, adjusted_close=90.0 + index, volume=2200.0 + index))
        rows.append(_sample_v2_row(ticker='GOD.OL', price_date=day, raw_close=140.0 + index, adjusted_close=100.0 + index, volume=2400.0 + index))
        rows.append(_sample_v2_row(ticker='OSEBX.OL', price_date=day, raw_close=300.0 + index, adjusted_close=260.0 + index, volume=4000.0 + index))
    write_market_data_rows_for_test(db_path=path, rows=rows, allow_test_db_write=True)


def _build_incumbent_universe_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE universe_cache (universe_key TEXT PRIMARY KEY, tickers_json TEXT, source_label TEXT, updated_at TEXT)')
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            ('NORWAY_V2', json.dumps(['CAMBI.OL', 'SNTIA.OL', 'GOD.OL', 'OSEBX.OL']), 'fixture', '2026-09-15T00:00:00Z'),
        )


class ScreenerUiOrchestrationTests(unittest.TestCase):
    def test_orchestration_returns_expected_summary_from_synthetic_db(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_fixture_db(db_path)
            result = build_minimal_screener_result(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            self.assertEqual(result.universe_source, 'universe_cache:NORWAY_V2')
            self.assertEqual(result.price_table, PRICE_TABLE_LEGACY)
            self.assertEqual(result.close_input_source, 'close')
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

    def test_selected_ticker_detail_model_can_be_built_from_synthetic_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_fixture_db(db_path)
            result = build_minimal_screener_result(db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='^OSEAX')
            detail = build_selected_ticker_detail(result, ticker=result.rows[0].ticker, include_avoid=True)
            self.assertEqual(detail.ticker, result.rows[0].ticker)
            self.assertEqual(detail.raw_rank, result.rows[0].raw_rank)

    def test_selected_ticker_chart_data_loads_only_selected_ticker_plus_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_fixture_db(db_path)
            chart = build_selected_ticker_chart_detail(db_path=db_path, ticker='LEADER.OL', benchmark_ticker='^OSEAX')
            self.assertEqual(set(chart.loaded_tickers), {'LEADER.OL', '^OSEAX'})

    def test_chart_data_aligns_ticker_and_benchmark_dates_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_fixture_db(db_path)
            chart = build_selected_ticker_chart_detail(db_path=db_path, ticker='LEADER.OL', benchmark_ticker='^OSEAX')
            self.assertTrue(all(point.indexed_benchmark is not None for point in chart.price_points))
            dates = [point.price_date for point in chart.price_points]
            self.assertEqual(dates, sorted(dates))

    def test_indexed_chart_starts_at_100_for_ticker_and_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_fixture_db(db_path)
            chart = build_selected_ticker_chart_detail(db_path=db_path, ticker='LEADER.OL', benchmark_ticker='^OSEAX')
            first = chart.price_points[0]
            self.assertEqual(round(first.indexed_close or 0.0, 6), 100.0)
            self.assertEqual(round(first.indexed_benchmark or 0.0, 6), 100.0)

    def test_missing_benchmark_data_is_handled_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            _build_fixture_db(db_path)
            chart = build_selected_ticker_chart_detail(db_path=db_path, ticker='LEADER.OL', benchmark_ticker='^MISSING')
            self.assertIsNotNone(chart.warning)

    def test_v2_mode_runs_with_explicit_tickers_without_universe_cache(self) -> None:
        db_path = Path('/tmp/tradetool_v2_screener_ui_explicit.sqlite')
        if db_path.exists():
            db_path.unlink()
        _build_fixture_v2_db(db_path)
        result = build_minimal_screener_result(
            db_path=db_path,
            universe_id='EXPLICIT_V2_TEST',
            explicit_tickers=['CAMBI.OL', 'SNTIA.OL', 'GOD.OL'],
            benchmark_ticker='OSEBX.OL',
            price_table=PRICE_TABLE_V2,
            data_source='yahoo',
        )
        self.assertEqual(result.universe_source, 'explicit_tickers')
        self.assertEqual(result.price_table, PRICE_TABLE_V2)
        self.assertEqual(result.data_source, 'yahoo')
        self.assertEqual(result.close_input_source, 'adjusted_close')
        self.assertEqual(result.input_universe_count, 3)
        self.assertEqual(result.feature_complete_count, 3)
        self.assertEqual(result.ranked_count, 3)
        db_path.unlink()

    def test_v2_mode_maps_adjusted_close_to_latest_close_and_keeps_no_ml_or_holdings_output(self) -> None:
        db_path = Path('/tmp/tradetool_v2_screener_ui_adjusted.sqlite')
        if db_path.exists():
            db_path.unlink()
        _build_fixture_v2_db(db_path)
        result = build_minimal_screener_result(
            db_path=db_path,
            universe_id='EXPLICIT_V2_TEST',
            explicit_tickers=['CAMBI.OL'],
            benchmark_ticker='OSEBX.OL',
            price_table=PRICE_TABLE_V2,
            data_source='yahoo',
        )
        row = result.rows[0].to_dict()
        self.assertEqual(row['latest_close'], 339.0)
        self.assertNotIn('ml_score', row)
        self.assertNotIn('holdings_signal', row)
        db_path.unlink()

    def test_v2_chart_uses_price_history_v2_reader_and_adjusted_close(self) -> None:
        db_path = Path('/tmp/tradetool_v2_screener_ui_chart.sqlite')
        if db_path.exists():
            db_path.unlink()
        _build_fixture_v2_db(db_path)
        chart = build_selected_ticker_chart_detail(
            db_path=db_path,
            ticker='CAMBI.OL',
            benchmark_ticker='OSEBX.OL',
            price_table=PRICE_TABLE_V2,
            data_source='yahoo',
        )
        self.assertEqual(set(chart.loaded_tickers), {'CAMBI.OL', 'OSEBX.OL'})
        self.assertTrue(all(point.ticker == 'CAMBI.OL' for point in chart.price_points))
        self.assertEqual(chart.price_points[-1].close, 339.0)
        self.assertIsNone(chart.warning)
        price_spec = build_price_chart_spec(chart)
        benchmark_spec = build_relative_strength_chart_spec(chart)
        self.assertIsNotNone(price_spec)
        self.assertIsNotNone(benchmark_spec)
        assert price_spec is not None
        assert benchmark_spec is not None
        self.assertGreater(len(price_spec['data']['values']), 0)
        self.assertEqual({row['ticker'] for row in price_spec['data']['values']}, {'CAMBI.OL'})
        self.assertEqual(price_spec['transform'][0]['fold'], ['close', 'sma50', 'sma200'])
        self.assertEqual(benchmark_spec['vconcat'][0]['transform'][0]['fold'], ['indexed_close', 'indexed_benchmark'])
        db_path.unlink()

    def test_v2_chart_period_changes_visible_range_only(self) -> None:
        db_path = Path('/tmp/tradetool_v2_screener_ui_chart_period.sqlite')
        universe_path = Path('/tmp/tradetool_v2_screener_ui_chart_period_universe.sqlite')
        for path in (db_path, universe_path):
            if path.exists():
                path.unlink()
        _build_fixture_v2_db(db_path)
        _build_incumbent_universe_db(universe_path)
        one_year_chart = build_selected_ticker_chart_detail(
            db_path=db_path,
            ticker='CAMBI.OL',
            benchmark_ticker='OSEBX.OL',
            price_table=PRICE_TABLE_V2,
            data_source='yahoo',
            chart_period_label=DEFAULT_CHART_PERIOD_LABEL,
        )
        three_month_chart = build_selected_ticker_chart_detail(
            db_path=db_path,
            ticker='CAMBI.OL',
            benchmark_ticker='OSEBX.OL',
            price_table=PRICE_TABLE_V2,
            data_source='yahoo',
            chart_period_label='3 mnd',
        )
        self.assertEqual(len(three_month_chart.price_points), CHART_PERIOD_ROW_COUNTS['3 mnd'])
        self.assertGreater(len(one_year_chart.price_points), len(three_month_chart.price_points))
        self.assertNotEqual(one_year_chart.requested_start_date, three_month_chart.requested_start_date)

        before_rows = incumbent_screener_table_rows(
            build_incumbent_screener_ui_result(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='OSEBX.OL',
                as_of_date=date(2025, 9, 17),
                data_source='yahoo',
                top_n=3,
                universe_db_path=universe_path,
            )
        )
        build_selected_ticker_chart_detail(
            db_path=db_path,
            ticker='CAMBI.OL',
            benchmark_ticker='OSEBX.OL',
            price_table=PRICE_TABLE_V2,
            data_source='yahoo',
            chart_period_label='2 år',
        )
        after_rows = incumbent_screener_table_rows(
            build_incumbent_screener_ui_result(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='OSEBX.OL',
                as_of_date=date(2025, 9, 17),
                data_source='yahoo',
                top_n=3,
                universe_db_path=universe_path,
            )
        )
        self.assertEqual([row['Ticker'] for row in before_rows], [row['Ticker'] for row in after_rows])
        db_path.unlink()
        universe_path.unlink()

    def test_v2_chart_calculates_sma_before_visible_trim(self) -> None:
        db_path = Path('/tmp/tradetool_v2_screener_ui_chart_sma.sqlite')
        if db_path.exists():
            db_path.unlink()
        initialize_market_data_schema(db_path)
        rows: list[MarketDataRow] = []
        start = date(2025, 1, 1)
        for index in range(320):
            day = (start + timedelta(days=index)).isoformat()
            rows.append(_sample_v2_row(ticker='CAMBI.OL', price_date=day, raw_close=100.0 + index, adjusted_close=100.0 + index))
            rows.append(_sample_v2_row(ticker='OSEBX.OL', price_date=day, raw_close=300.0 + index, adjusted_close=300.0 + index))
        write_market_data_rows_for_test(db_path=db_path, rows=rows, allow_test_db_write=True)

        chart = build_selected_ticker_chart_detail(
            db_path=db_path,
            ticker='CAMBI.OL',
            benchmark_ticker='OSEBX.OL',
            price_table=PRICE_TABLE_V2,
            data_source='yahoo',
            chart_period_label='3 mnd',
        )
        self.assertEqual(len(chart.price_points), CHART_PERIOD_ROW_COUNTS['3 mnd'])
        self.assertEqual(chart.sma200_non_null_count, len(chart.price_points))
        self.assertIsNotNone(chart.price_points[0].sma200)
        db_path.unlink()

    def test_v2_chart_normalization_and_rs_math_start_at_100(self) -> None:
        db_path = Path('/tmp/tradetool_v2_screener_ui_chart_math.sqlite')
        if db_path.exists():
            db_path.unlink()
        _build_fixture_v2_db(db_path)
        chart = build_selected_ticker_chart_detail(
            db_path=db_path,
            ticker='CAMBI.OL',
            benchmark_ticker='OSEBX.OL',
            price_table=PRICE_TABLE_V2,
            data_source='yahoo',
            chart_period_label='6 mnd',
        )
        first = chart.price_points[0]
        last = chart.price_points[-1]
        self.assertEqual(chart.first_normalized_date, first.price_date)
        self.assertAlmostEqual(chart.first_indexed_ticker_value or 0.0, 100.0, places=6)
        self.assertAlmostEqual(chart.first_indexed_benchmark_value or 0.0, 100.0, places=6)
        self.assertAlmostEqual(chart.first_rs_index_value or 0.0, 100.0, places=6)
        self.assertAlmostEqual(first.relative_strength_line or 0.0, 100.0, places=6)
        expected_rs = ((last.indexed_close or 0.0) / (last.indexed_benchmark or 1.0)) * 100.0
        self.assertAlmostEqual(last.relative_strength_line or 0.0, expected_rs, places=6)
        db_path.unlink()

    def test_v2_chart_specs_use_user_friendly_series_labels(self) -> None:
        db_path = Path('/tmp/tradetool_v2_screener_ui_chart_labels.sqlite')
        if db_path.exists():
            db_path.unlink()
        _build_fixture_v2_db(db_path)
        chart = build_selected_ticker_chart_detail(
            db_path=db_path,
            ticker='CAMBI.OL',
            benchmark_ticker='OSEBX.OL',
            price_table=PRICE_TABLE_V2,
            data_source='yahoo',
        )
        price_spec = build_price_chart_spec(chart)
        benchmark_spec = build_relative_strength_chart_spec(chart)
        assert price_spec is not None and benchmark_spec is not None
        self.assertEqual(price_spec['encoding']['color']['field'], 'Serie')
        self.assertIn("'Kurs'", price_spec['transform'][1]['calculate'])
        self.assertIn("'SMA50'", price_spec['transform'][1]['calculate'])
        self.assertIn("'SMA200'", price_spec['transform'][1]['calculate'])
        self.assertIn("'CAMBI.OL'", benchmark_spec['vconcat'][0]['transform'][1]['calculate'])
        self.assertIn("'OSEBX.OL'", benchmark_spec['vconcat'][0]['transform'][1]['calculate'])
        self.assertEqual(benchmark_spec['vconcat'][1]['encoding']['y']['title'], 'Relativ styrke-indeks mot benchmark')
        db_path.unlink()

    def test_v2_chart_data_changes_with_selected_ticker(self) -> None:
        db_path = Path('/tmp/tradetool_v2_screener_ui_chart_selection.sqlite')
        if db_path.exists():
            db_path.unlink()
        _build_fixture_v2_db(db_path)
        cambi_chart = build_selected_ticker_chart_detail(
            db_path=db_path,
            ticker='CAMBI.OL',
            benchmark_ticker='OSEBX.OL',
            price_table=PRICE_TABLE_V2,
            data_source='yahoo',
            max_price_date=date(2025, 9, 17),
        )
        sntia_chart = build_selected_ticker_chart_detail(
            db_path=db_path,
            ticker='SNTIA.OL',
            benchmark_ticker='OSEBX.OL',
            price_table=PRICE_TABLE_V2,
            data_source='yahoo',
            max_price_date=date(2025, 9, 17),
        )
        cambi_spec = build_price_chart_spec(cambi_chart)
        sntia_spec = build_price_chart_spec(sntia_chart)
        assert cambi_spec is not None and sntia_spec is not None

        self.assertEqual({row['ticker'] for row in cambi_spec['data']['values']}, {'CAMBI.OL'})
        self.assertEqual({row['ticker'] for row in sntia_spec['data']['values']}, {'SNTIA.OL'})
        self.assertNotEqual(cambi_spec['data']['values'][-1]['close'], sntia_spec['data']['values'][-1]['close'])
        self.assertNotEqual(cambi_chart.ticker, sntia_chart.ticker)
        db_path.unlink()

    def test_v2_chart_respects_as_of_cap(self) -> None:
        db_path = Path('/tmp/tradetool_v2_screener_ui_chart_cap.sqlite')
        if db_path.exists():
            db_path.unlink()
        _build_fixture_v2_db(db_path)
        chart = build_selected_ticker_chart_detail(
            db_path=db_path,
            ticker='CAMBI.OL',
            benchmark_ticker='OSEBX.OL',
            price_table=PRICE_TABLE_V2,
            data_source='yahoo',
            max_price_date=date(2025, 7, 1),
        )
        self.assertLessEqual(max(point.price_date for point in chart.price_points), '2025-07-01')
        db_path.unlink()

    def test_v2_chart_missing_benchmark_reports_rows_in_warning(self) -> None:
        db_path = Path('/tmp/tradetool_v2_screener_ui_chart_missing_benchmark.sqlite')
        if db_path.exists():
            db_path.unlink()
        _build_fixture_v2_db(db_path)
        chart = build_selected_ticker_chart_detail(
            db_path=db_path,
            ticker='CAMBI.OL',
            benchmark_ticker='MISSING.OL',
            price_table=PRICE_TABLE_V2,
            data_source='yahoo',
        )
        self.assertIsNotNone(chart.warning)
        self.assertIn('Rader funnet for ticker: 260', str(chart.warning))
        self.assertIn('Rader funnet for benchmark: 0', str(chart.warning))
        self.assertGreater(len(chart.price_points), 0)
        self.assertIsNotNone(build_price_chart_spec(chart))
        db_path.unlink()

    def test_v2_mode_does_not_write_to_database(self) -> None:
        db_path = Path('/tmp/tradetool_v2_screener_ui_nowrite.sqlite')
        if db_path.exists():
            db_path.unlink()
        _build_fixture_v2_db(db_path)
        before = initialize_market_data_schema(db_path).row_count
        build_minimal_screener_result(
            db_path=db_path,
            universe_id='EXPLICIT_V2_TEST',
            explicit_tickers=['CAMBI.OL'],
            benchmark_ticker='OSEBX.OL',
            price_table=PRICE_TABLE_V2,
            data_source='yahoo',
        )
        build_selected_ticker_chart_detail(
            db_path=db_path,
            ticker='CAMBI.OL',
            benchmark_ticker='OSEBX.OL',
            price_table=PRICE_TABLE_V2,
            data_source='yahoo',
        )
        after = initialize_market_data_schema(db_path).row_count
        self.assertEqual(before, after)
        db_path.unlink()

    def test_incumbent_screener_ui_uses_core_and_outputs_risk_fields(self) -> None:
        db_path = Path('/tmp/tradetool_v2_incumbent_ui.sqlite')
        universe_path = Path('/tmp/tradetool_v2_incumbent_ui_universe.sqlite')
        for path in (db_path, universe_path):
            if path.exists():
                path.unlink()
        _build_fixture_v2_db(db_path)
        _build_incumbent_universe_db(universe_path)
        result = build_incumbent_screener_ui_result(
            db_path=db_path,
            universe_id='NORWAY_V2',
            benchmark_ticker='OSEBX.OL',
            as_of_date=date(2025, 9, 17),
            data_source='yahoo',
            top_n=2,
            universe_db_path=universe_path,
        )
        rows = incumbent_screener_table_rows(result)
        self.assertEqual(result.baseline_id, 'incumbent_naive_rs_6m_top_10_v0')
        self.assertEqual(len(rows), 2)
        self.assertEqual(list(rows[0]), [
            'Rang',
            'Ticker',
            'RS 6m',
            'RS 3m',
            '6m %',
            '3m %',
            'Siste kurs',
            'Risiko',
            'Risikotagger',
        ])
        self.assertIn(rows[0]['Risiko'], {'LOW', 'MEDIUM', 'HIGH'})
        self.assertNotIn('Forklaring', rows[0])
        self.assertNotIn('ml_score', rows[0])
        self.assertNotIn('holdings_signal', rows[0])
        selected = selected_incumbent_candidate(result, ticker=str(rows[0]['Ticker']))
        detail_rows = incumbent_candidate_detail_rows(selected)
        self.assertIn({'felt': 'Ticker', 'verdi': rows[0]['Ticker']}, detail_rows)
        self.assertTrue(any(row['felt'] == 'Risikotagger' for row in detail_rows))
        self.assertIsInstance(incumbent_candidate_explanation(selected), str)
        self.assertEqual(incumbent_screener_ticker_options(rows), [str(row['Ticker']) for row in rows])
        self.assertEqual(resolve_incumbent_selected_ticker(rows, selected_row_indexes=[1]), str(rows[1]['Ticker']))
        self.assertEqual(resolve_incumbent_selected_ticker(rows, current_ticker=''), str(rows[0]['Ticker']))
        summary = incumbent_screener_summary_rows(result)
        self.assertIn({'felt': 'Univers', 'verdi': 'NORWAY_V2'}, summary)
        db_path.unlink()
        universe_path.unlink()

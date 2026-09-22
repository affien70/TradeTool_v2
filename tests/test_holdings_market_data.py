from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.data.market_data_schema import MarketDataRow, initialize_market_data_schema, write_market_data_rows_for_test
from tradetool.holdings import (
    HoldingSettings,
    PositionState,
    build_holding_signal_inputs_from_v2_price_history,
    evaluate_holding_signal,
)


class HoldingsMarketDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(dir='/tmp')
        self.db_path = Path(self.temp_dir.name) / 'market.sqlite'
        initialize_market_data_schema(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ready_path_honors_configured_sma_rs_benchmark_cost_and_atr_inputs(self) -> None:
        self._seed_histories(
            ticker='STOCK.OL',
            benchmark='CUSTOM.OL',
            stock_closes=[100.0 + index for index in range(180)] + [320.0] * 19 + [280.0],
            benchmark_closes=[200.0 - (index * 0.5) for index in range(200)],
        )
        result = build_holding_signal_inputs_from_v2_price_history(
            db_path=self.db_path,
            ticker='stock.ol',
            position=self._position(gav=150.0),
            settings=HoldingSettings(
                sell_fast_sma_days=3,
                rs_months=1,
                sell_drop_from_peak=True,
                norway_benchmark_id='CUSTOM.OL',
            ),
            entry_date=date(2025, 6, 1),
        )
        self.assertTrue(result.ready)
        self.assertEqual(result.benchmark_id, 'CUSTOM.OL')
        self.assertEqual(result.as_of_date, date(2025, 7, 19))
        self.assertIsNotNone(result.signal_inputs)
        inputs = result.signal_inputs
        self.assertTrue(inputs.below_fast_sma)
        self.assertFalse(inputs.below_long_sma)
        self.assertFalse(inputs.weak_rs)
        self.assertFalse(inputs.below_cost_basis)
        self.assertTrue(inputs.drop_from_peak)
        self.assertGreater(inputs.short_term_return or 0.0, 0.0)
        self.assertTrue(inputs.fast_sma_slope_positive)

    def test_unknown_cost_does_not_fabricate_a_cost_stop(self) -> None:
        self._seed_histories(ticker='STOCK.OL', benchmark='OSEBX.OL')
        result = build_holding_signal_inputs_from_v2_price_history(
            db_path=self.db_path,
            ticker='STOCK.OL',
            position=self._position(gav=None, cost_basis_status='unknown'),
            settings=HoldingSettings(sell_fast_sma_days=3, rs_months=1),
        )
        self.assertTrue(result.ready)
        self.assertFalse(result.signal_inputs.below_cost_basis)

    def test_missing_stock_and_benchmark_are_unavailable(self) -> None:
        stock_missing = build_holding_signal_inputs_from_v2_price_history(
            db_path=self.db_path,
            ticker='MISSING.OL',
            position=self._position(),
            settings=HoldingSettings(),
        )
        self.assertEqual(stock_missing.unavailable_reasons, ('stock_market_data_missing', 'benchmark_market_data_missing'))
        self._seed_histories(ticker='STOCK.OL', benchmark='OTHER.OL')
        benchmark_missing = build_holding_signal_inputs_from_v2_price_history(
            db_path=self.db_path,
            ticker='STOCK.OL',
            position=self._position(),
            settings=HoldingSettings(norway_benchmark_id='MISSING.OL'),
        )
        self.assertEqual(benchmark_missing.unavailable_reasons, ('benchmark_market_data_missing',))

    def test_insufficient_sma_rs_and_atr_history_are_unavailable(self) -> None:
        self._seed_histories(ticker='SHORT.OL', benchmark='OSEBX.OL', row_count=13)
        short = build_holding_signal_inputs_from_v2_price_history(
            db_path=self.db_path,
            ticker='SHORT.OL',
            position=self._position(),
            settings=HoldingSettings(sell_fast_sma_days=3, rs_months=1, sell_drop_from_peak=True),
            entry_date=date(2025, 1, 1),
        )
        self.assertIn('insufficient_sma200_history', short.unavailable_reasons)
        self.assertIn('insufficient_rs_history', short.unavailable_reasons)
        self.assertIn('insufficient_atr_history', short.unavailable_reasons)
        self._seed_histories(ticker='LATE.OL', benchmark='OSEBX.OL')
        late_entry = build_holding_signal_inputs_from_v2_price_history(
            db_path=self.db_path,
            ticker='LATE.OL',
            position=self._position(),
            settings=HoldingSettings(sell_fast_sma_days=3, rs_months=1, sell_drop_from_peak=True),
            entry_date=date(2025, 7, 10),
        )
        self.assertIn('insufficient_atr_history', late_entry.unavailable_reasons)

    def test_entry_metadata_is_required_only_for_enabled_trailing_stop(self) -> None:
        self._seed_histories(ticker='STOCK.OL', benchmark='OSEBX.OL')
        disabled = build_holding_signal_inputs_from_v2_price_history(
            db_path=self.db_path,
            ticker='STOCK.OL',
            position=self._position(gav=None, cost_basis_status='unknown'),
            settings=HoldingSettings(sell_fast_sma_days=3, rs_months=1),
        )
        self.assertTrue(disabled.ready)
        enabled = build_holding_signal_inputs_from_v2_price_history(
            db_path=self.db_path,
            ticker='STOCK.OL',
            position=self._position(),
            settings=HoldingSettings(sell_fast_sma_days=3, rs_months=1, sell_drop_from_peak=True),
        )
        self.assertIn('required_entry_metadata_unavailable', enabled.unavailable_reasons)

    def test_adapter_inputs_drive_existing_h2b_hold_watch_and_sell_results(self) -> None:
        settings = HoldingSettings(sell_fast_sma_days=3, rs_months=1)
        for ticker, closes, gav, expected_action in (
            ('HOLD.OL', [100.0 + index for index in range(200)], 50.0, 'HOLD'),
            ('WATCH.OL', [100.0 + index for index in range(180)] + [320.0] * 19 + [280.0], 50.0, 'FØLG MED'),
            ('SELL.OL', [100.0 + index for index in range(200)], 500.0, 'SELL'),
        ):
            with self.subTest(ticker=ticker):
                self._seed_histories(ticker=ticker, benchmark='OSEBX.OL', stock_closes=closes)
                if ticker == 'WATCH.OL':
                    self._seed_histories(
                        ticker=ticker,
                        benchmark='OSEBX.OL',
                        stock_closes=closes,
                        benchmark_closes=[200.0 - (index * 0.5) for index in range(200)],
                    )
                result = build_holding_signal_inputs_from_v2_price_history(
                    db_path=self.db_path,
                    ticker=ticker,
                    position=self._position(gav=gav),
                    settings=settings,
                )
                self.assertTrue(result.ready)
                evaluation = evaluate_holding_signal(result.signal_inputs)
                self.assertEqual(evaluation.action, expected_action)

    def test_adapter_has_no_v1_ui_policy_or_write_dependencies(self) -> None:
        source = Path('src/tradetool/holdings/market_data.py').read_text(encoding='utf-8').lower()
        for forbidden in ('streamlit', 'tradetool.ui', 'tradetool.ranking', 'tradetool.policy', 'v1_import', 'sqlite3', 'initialize_'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def _seed_histories(
        self,
        *,
        ticker: str,
        benchmark: str,
        row_count: int = 200,
        stock_closes: list[float] | None = None,
        benchmark_closes: list[float] | None = None,
    ) -> None:
        stock = stock_closes or [100.0 + index for index in range(row_count)]
        benchmark_values = benchmark_closes or [100.0 + (index * 0.5) for index in range(len(stock))]
        start = date(2025, 1, 1)
        rows: list[MarketDataRow] = []
        for row_ticker, closes in ((ticker, stock), (benchmark, benchmark_values)):
            for index, close in enumerate(closes):
                price_date = start + timedelta(days=index)
                rows.append(MarketDataRow(
                    ticker=row_ticker,
                    price_date=price_date.isoformat(),
                    raw_open=close - 1.0,
                    raw_high=close + 2.0,
                    raw_low=close - 2.0,
                    raw_close=close,
                    adjusted_close=close,
                    volume=1000.0,
                    data_source='yahoo',
                    created_at_utc='2026-09-22T00:00:00Z',
                    updated_at_utc='2026-09-22T00:00:00Z',
                ))
        write_market_data_rows_for_test(db_path=self.db_path, rows=rows, allow_test_db_write=True)

    @staticmethod
    def _position(*, gav: float | None = 100.0, cost_basis_status: str | None = 'known') -> PositionState:
        return PositionState(
            open=True,
            open_quantity=10.0,
            priced_open_quantity=10.0 if gav is not None else 0.0,
            remaining_cost_basis=0.0 if gav is None else gav * 10.0,
            gav=gav,
            cost_basis_status=cost_basis_status,
        )


if __name__ == '__main__':
    unittest.main()
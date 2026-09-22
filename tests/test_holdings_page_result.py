from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.data.market_data_schema import MarketDataRow, initialize_market_data_schema, write_market_data_rows_for_test
from tradetool.holdings import (
    HoldingSettings,
    HoldingTransaction,
    HoldingTransactionRecord,
    build_holdings_page_result,
    initialize_holdings_schema,
    upsert_holding_transaction,
)


class HoldingsPageResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(dir='/tmp')
        self.market_db_path = Path(self.temp_dir.name) / 'market.sqlite'
        initialize_market_data_schema(self.market_db_path)
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        initialize_holdings_schema(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp_dir.cleanup()

    def test_rows_summary_and_detail_use_h2a_h2b_and_h4d_contracts(self) -> None:
        settings = HoldingSettings(sell_fast_sma_days=3, rs_months=1)
        self._seed_prices('HOLD.OL', [100.0 + index for index in range(200)])
        self._seed_prices('WATCH.OL', [100.0 + index for index in range(180)] + [320.0] * 19 + [280.0], benchmark=[200.0 - (index * 0.5) for index in range(200)])
        self._seed_prices('SELL.OL', [100.0 + index for index in range(200)])
        self._seed_prices('UNKNOWN.OL', [100.0 + index for index in range(200)])
        self._seed_prices('WATCH.OL', [100.0 + index for index in range(180)] + [320.0] * 19 + [280.0], benchmark=[200.0 - (index * 0.5) for index in range(200)])
        self._add_transaction('HOLD.OL', 'KJØPT', 10.0, 50.0, '2025-01-01')
        self._add_transaction('WATCH.OL', 'KJØPT', 10.0, 50.0, '2025-01-01')
        self._add_transaction('SELL.OL', 'KJØPT', 10.0, 500.0, '2025-01-01')
        self._add_transaction('UNKNOWN.OL', 'INNLEGG', 5.0, None, '2025-01-01', source_cost_basis_missing=True)
        self._add_transaction('CLOSED.OL', 'KJØPT', 4.0, 100.0, '2025-01-01')
        self._add_transaction('CLOSED.OL', 'SALG', 4.0, 120.0, '2025-02-01')

        result = build_holdings_page_result(
            holdings_connection=self.connection,
            market_data_db_path=self.market_db_path,
            settings=settings,
        )

        self.assertTrue(result.holdings_schema_ready)
        self.assertEqual(result.summary.open_position_count, 4)
        self.assertEqual(result.summary.hold_count, 2)
        self.assertEqual(result.summary.follow_up_count, 1)
        self.assertEqual(result.summary.sell_count, 1)
        self.assertEqual(result.summary.unavailable_count, 0)
        self.assertEqual(result.summary.unknown_cost_basis_position_count, 1)
        self.assertTrue(result.summary.has_unknown_cost_basis)
        self.assertEqual({row.ticker for row in result.rows}, {'HOLD.OL', 'WATCH.OL', 'SELL.OL', 'UNKNOWN.OL'})

        hold = self._row(result, 'HOLD.OL')
        self.assertEqual(hold.quantity, 10.0)
        self.assertEqual(hold.gav, 50.0)
        self.assertEqual(hold.cost_basis_status, 'known')
        self.assertEqual(hold.current_price, 299.0)
        self.assertEqual(hold.current_market_value, 2990.0)
        self.assertEqual(hold.unrealized_pnl_nok, 2490.0)
        self.assertEqual(hold.unrealized_pnl_pct, 4.98)
        self.assertEqual(hold.signal_action, 'HOLD')

        watch = self._row(result, 'WATCH.OL')
        self.assertEqual(watch.signal_action, 'FØLG MED')
        self.assertEqual(watch.signal_reasons, ('Kurs under SMA3, men RS, momentum og SMA3-trend er fortsatt sterke.',))
        sell = self._row(result, 'SELL.OL')
        self.assertEqual(sell.signal_action, 'SELL')
        self.assertEqual(sell.signal_reasons, ('10% under kostpris',))
        unknown = self._row(result, 'UNKNOWN.OL')
        self.assertEqual(unknown.cost_basis_status, 'unknown')
        self.assertIsNone(unknown.gav)
        self.assertIsNone(unknown.unrealized_pnl_nok)
        self.assertIsNone(unknown.unrealized_pnl_pct)

        detail = result.detail_for(hold.position_key)
        self.assertIsNotNone(detail)
        self.assertEqual(detail.position, detail.position)
        self.assertEqual(len(detail.source_transactions), 1)
        self.assertTrue(detail.market_data.ready)
        self.assertIsNotNone(detail.signal_evaluation)
        self.assertEqual(detail.chart.current_price, 299.0)
        self.assertIsNotNone(detail.chart.fast_sma)
        self.assertIsNotNone(detail.chart.sma200)
        self.assertIsNotNone(detail.chart.relative_strength)
        self.assertIsNotNone(detail.chart.short_term_return)
        self.assertEqual(len(detail.chart.points), 200)
        self.assertEqual(len(detail.chart.purchase_markers), 1)
        self.assertFalse(hasattr(detail.chart, 'to_plotly_json'))

    def test_partial_basis_is_preserved_and_excluded_from_full_pnl_summary(self) -> None:
        self._seed_prices('PARTIAL.OL', [100.0 + index for index in range(200)])
        self._add_transaction('PARTIAL.OL', 'KJØPT', 10.0, 100.0, '2025-01-01')
        self._add_transaction('PARTIAL.OL', 'INNLEGG', 5.0, None, '2025-02-01', source_cost_basis_missing=True)

        result = build_holdings_page_result(
            holdings_connection=self.connection,
            market_data_db_path=self.market_db_path,
            settings=HoldingSettings(),
        )

        row = self._row(result, 'PARTIAL.OL')
        self.assertEqual(row.quantity, 15.0)
        self.assertEqual(row.gav, 100.0)
        self.assertEqual(row.cost_basis_status, 'partially_unknown')
        self.assertIsNone(row.unrealized_pnl_nok)
        self.assertEqual(result.summary.partially_unknown_cost_basis_position_count, 1)
        self.assertEqual(result.summary.known_pnl_position_count, 0)
        self.assertIsNone(result.summary.total_known_unrealized_pnl_nok)

    def test_enabled_trailing_stop_exposes_existing_technical_detail(self) -> None:
        self._seed_prices('TRAIL.OL', [100.0 + index for index in range(200)])
        self._add_transaction('TRAIL.OL', 'KJØPT', 10.0, 100.0, '2025-01-01')

        result = build_holdings_page_result(
            holdings_connection=self.connection,
            market_data_db_path=self.market_db_path,
            settings=HoldingSettings(sell_drop_from_peak=True),
        )

        detail = result.detail_for(self._row(result, 'TRAIL.OL').position_key)
        self.assertIsNotNone(detail.chart.atr)
        self.assertIsNotNone(detail.chart.post_entry_peak)
        self.assertIsNotNone(detail.chart.trailing_stop)
        self.assertTrue(detail.chart.trailing_stop.active)

    def test_unavailable_market_data_keeps_open_position_visible(self) -> None:
        self._add_transaction('MISSING.OL', 'KJØPT', 2.0, 100.0, '2025-01-01')

        result = build_holdings_page_result(
            holdings_connection=self.connection,
            market_data_db_path=self.market_db_path,
            settings=HoldingSettings(),
        )

        row = self._row(result, 'MISSING.OL')
        self.assertIsNone(row.signal_action)
        self.assertIn('stock_market_data_missing', row.unavailable_reasons)
        self.assertIn('benchmark_market_data_missing', row.unavailable_reasons)
        self.assertEqual(result.summary.unavailable_count, 1)
        self.assertIsNone(row.current_market_value)

    def test_missing_schema_and_no_open_positions_are_explicit(self) -> None:
        connection = sqlite3.connect(':memory:')
        connection.row_factory = sqlite3.Row
        try:
            missing_schema = build_holdings_page_result(
                holdings_connection=connection,
                market_data_db_path=self.market_db_path,
            )
        finally:
            connection.close()
        self.assertFalse(missing_schema.holdings_schema_ready)
        self.assertEqual(missing_schema.unavailable_reasons, ('holdings_schema_missing',))

        no_open_positions = build_holdings_page_result(
            holdings_connection=self.connection,
            market_data_db_path=self.market_db_path,
        )
        self.assertTrue(no_open_positions.holdings_schema_ready)
        self.assertEqual(no_open_positions.unavailable_reasons, ('no_open_positions',))

    def test_page_result_has_no_ui_v1_screener_or_write_dependencies(self) -> None:
        source = Path('src/tradetool/holdings/page_result.py').read_text(encoding='utf-8').lower()
        for forbidden in ('streamlit', 'plotly', 'v1_import', 'tradetool.ui', 'tradetool.ranking', 'tradetool.policy', 'initialize_', 'upsert_'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def _seed_prices(self, ticker: str, closes: list[float], *, benchmark: list[float] | None = None) -> None:
        benchmark_closes = benchmark or [100.0 + (index * 0.5) for index in range(len(closes))]
        start = date(2025, 1, 1)
        rows: list[MarketDataRow] = []
        for row_ticker, values in ((ticker, closes), ('OSEBX.OL', benchmark_closes)):
            for index, close in enumerate(values):
                rows.append(MarketDataRow(
                    ticker=row_ticker,
                    price_date=(start + timedelta(days=index)).isoformat(),
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
        write_market_data_rows_for_test(db_path=self.market_db_path, rows=rows, allow_test_db_write=True)

    def _add_transaction(
        self,
        ticker: str,
        transaction_type: str,
        shares: float,
        price: float | None,
        trade_date: str,
        *,
        source_cost_basis_missing: bool = False,
    ) -> None:
        transaction = HoldingTransaction(
            transaction_type=transaction_type,
            shares=shares,
            price=price,
            amount=None if price is None else abs(shares) * price,
            trade_date=trade_date,
            settlement_date=trade_date,
            isin=f'NO-{ticker}',
            ticker=ticker,
            instrument_name=f'{ticker} name',
            currency='NOK',
        )
        record = HoldingTransactionRecord.from_transaction(
            transaction,
            source_cost_basis_missing=source_cost_basis_missing,
        )
        upsert_holding_transaction(self.connection, record)

    @staticmethod
    def _row(result, ticker: str):
        return next(row for row in result.rows if row.ticker == ticker)


if __name__ == '__main__':
    unittest.main()
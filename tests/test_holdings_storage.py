from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tradetool.holdings import (
    DEFAULT_NORWAY_BENCHMARK_ID,
    HOLDINGS_SETTINGS_TABLE,
    HOLDINGS_TRANSACTIONS_TABLE,
    HoldingSettings,
    HoldingTransaction,
    HoldingTransactionRecord,
    initialize_holdings_schema,
    load_holding_settings,
    load_holding_transactions,
    reconstruct_position,
    save_holding_settings,
    upsert_holding_transaction,
)


class HoldingsStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / 'holdings_test.sqlite'
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        initialize_holdings_schema(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp_dir.cleanup()

    def test_schema_contains_only_approved_holdings_tables_and_columns(self) -> None:
        tables = {
            row['name']
            for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        self.assertEqual(tables, {HOLDINGS_TRANSACTIONS_TABLE, HOLDINGS_SETTINGS_TABLE})
        transaction_columns = {
            row['name'] for row in self.connection.execute(f'PRAGMA table_info("{HOLDINGS_TRANSACTIONS_TABLE}")')
        }
        self.assertEqual(
            transaction_columns,
            {
                'transaction_id', 'nordnet_transaction_id', 'fallback_transaction_key',
                'trade_date', 'settlement_date', 'isin', 'ticker', 'instrument_name',
                'ticker_source', 'note', 'transaction_type', 'shares', 'price', 'amount',
                'fee', 'currency', 'nordnet_result', 'source_cost_basis_missing',
                'created_at_utc', 'updated_at_utc',
            },
        )
        settings_columns = {
            row['name'] for row in self.connection.execute(f'PRAGMA table_info("{HOLDINGS_SETTINGS_TABLE}")')
        }
        self.assertEqual(
            settings_columns,
            {
                'settings_scope', 'period_label', 'rs_months', 'sell_rs_weak',
                'sell_below_cost_basis', 'sell_drop_from_peak', 'sell_fast_sma_days',
                'atr_multiplier', 'rs_threshold', 'norway_benchmark_id', 'updated_at_utc',
            },
        )
        indexes = self.connection.execute(f'PRAGMA index_list("{HOLDINGS_TRANSACTIONS_TABLE}")').fetchall()
        self.assertTrue(any(row['unique'] and row['partial'] for row in indexes))

    def test_transaction_round_trip_preserves_source_and_audit_fields(self) -> None:
        record = self._record(note='Imported account note', fee=9.5, nordnet_result=15.0, source_cost_basis_missing=True)
        saved = upsert_holding_transaction(self.connection, record)
        loaded, = load_holding_transactions(self.connection)
        self.assertEqual(saved, loaded)
        self.assertEqual(loaded.note, 'Imported account note')
        self.assertEqual(loaded.fee, 9.5)
        self.assertEqual(loaded.nordnet_result, 15.0)
        self.assertTrue(loaded.source_cost_basis_missing)

    def test_broker_identity_upsert_keeps_one_logical_transaction(self) -> None:
        first = self._record(nordnet_transaction_id='nordnet-1', amount=1000.0)
        replacement = self._record(nordnet_transaction_id='nordnet-1', amount=1015.0, price=101.5)
        upsert_holding_transaction(self.connection, first)
        upsert_holding_transaction(self.connection, replacement)
        transactions = load_holding_transactions(self.connection)
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0].transaction.price, 101.5)
        self.assertEqual(transactions[0].transaction.amount, 1015.0)

    def test_fallback_identity_upsert_ignores_price_and_amount_for_idempotency(self) -> None:
        first = self._record(nordnet_transaction_id=None, amount=1000.0)
        replacement = self._record(nordnet_transaction_id=None, amount=1015.0, price=101.5)
        self.assertEqual(first.fallback_key, replacement.fallback_key)
        upsert_holding_transaction(self.connection, first)
        upsert_holding_transaction(self.connection, replacement)
        transactions = load_holding_transactions(self.connection)
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0].transaction.amount, 1015.0)

    def test_transactions_read_in_h2a_deterministic_order_without_semantic_mutation(self) -> None:
        sale = self._record(transaction_type='SALG', shares=4.0, amount=480.0, trade_date='2025-01-03')
        purchase = self._record(transaction_type='KJØPT', shares=10.0, amount=1000.0, trade_date='2025-01-02')
        upsert_holding_transaction(self.connection, sale)
        upsert_holding_transaction(self.connection, purchase)
        stored = load_holding_transactions(self.connection)
        self.assertEqual([record.transaction.transaction_type for record in stored], ['KJØPT', 'SALG'])
        position = reconstruct_position(record.transaction for record in stored)
        self.assertTrue(position.open)
        self.assertEqual(position.open_quantity, 6.0)
        self.assertEqual(position.remaining_cost_basis, 600.0)

    def test_settings_defaults_require_no_row_and_use_osebx(self) -> None:
        self.assertEqual(load_holding_settings(self.connection), HoldingSettings())
        self.assertEqual(load_holding_settings(self.connection).norway_benchmark_id, DEFAULT_NORWAY_BENCHMARK_ID)
        self.assertEqual(load_holding_settings(self.connection).period_key, '1y')

    def test_settings_round_trip_preserves_typed_values_and_benchmark_override(self) -> None:
        settings = HoldingSettings(
            period_label='5 år',
            rs_months=12,
            sell_rs_weak=False,
            sell_below_cost_basis=False,
            sell_drop_from_peak=True,
            sell_fast_sma_days=150,
            atr_multiplier=3.75,
            rs_threshold=1.1,
            norway_benchmark_id='^OSEAX',
        )
        self.assertEqual(save_holding_settings(self.connection, settings), settings)
        self.assertEqual(load_holding_settings(self.connection), settings)
        self.assertEqual(load_holding_settings(self.connection).period_key, '5y')

    def _record(self, **overrides: object) -> HoldingTransactionRecord:
        payload: dict[str, object] = {
            'transaction_type': 'KJØPT',
            'shares': 10.0,
            'price': 100.0,
            'amount': 1000.0,
            'trade_date': '2025-01-02',
            'settlement_date': '2025-01-06',
            'isin': 'NO-SYNTH-01',
            'ticker': 'SYNTH.OL',
            'instrument_name': 'Synthetic Holding',
            'currency': 'NOK',
            'nordnet_transaction_id': None,
        }
        record_fields = {'ticker_source', 'note', 'fee', 'nordnet_result', 'source_cost_basis_missing'}
        transaction_fields = set(payload) - {'nordnet_transaction_id'} | {'nordnet_transaction_id'}
        transaction_payload = {field: overrides.get(field, value) for field, value in payload.items()}
        transaction = HoldingTransaction(**transaction_payload)
        record_payload = {field: overrides[field] for field in record_fields if field in overrides}
        unexpected = set(overrides) - transaction_fields - record_fields
        if unexpected:
            raise AssertionError(f'Unexpected test override: {unexpected}')
        return HoldingTransactionRecord.from_transaction(transaction, **record_payload)


if __name__ == '__main__':
    unittest.main()
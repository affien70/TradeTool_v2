from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tradetool.holdings import (
    HoldingTransaction,
    fallback_transaction_key,
    dry_run_v1_holdings_import,
    import_v1_holdings,
    initialize_holdings_schema,
    load_holding_transactions,
    reconstruct_position,
)


class HoldingsV1ImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.v1_path = Path(self.temp_dir.name) / 'v1_source.sqlite'
        self.v2_path = Path(self.temp_dir.name) / 'v2_target.sqlite'
        self._create_v1_schema()
        self.v2_connection = sqlite3.connect(self.v2_path)
        self.v2_connection.row_factory = sqlite3.Row
        initialize_holdings_schema(self.v2_connection)

    def tearDown(self) -> None:
        self.v2_connection.close()
        self.temp_dir.cleanup()

    def test_dry_run_maps_rows_without_writing_or_exposing_transaction_payloads(self) -> None:
        self._insert_v1_row(note='Private import note')
        v2_size_before = self.v2_path.stat().st_size
        result = dry_run_v1_holdings_import(self.v1_path, target_connection=self.v2_connection)
        self.assertEqual(result.source_rows, 1)
        self.assertEqual(result.mapped_rows, 1)
        self.assertEqual(result.invalid_rows, 0)
        self.assertEqual(result.would_insert, 1)
        self.assertEqual(result.written_rows, 0)
        self.assertEqual(load_holding_transactions(self.v2_connection), ())
        self.assertEqual(self.v2_path.stat().st_size, v2_size_before)
        self.assertNotIn('Private import note', repr(result))
        self.assertNotIn('SYNTH.OL', repr(result))

    def test_dry_run_counts_invalid_rows_and_never_writes(self) -> None:
        self._insert_v1_row(buy_date=None)
        result = dry_run_v1_holdings_import(self.v1_path, target_connection=self.v2_connection)
        self.assertEqual(result.source_rows, 1)
        self.assertEqual(result.mapped_rows, 0)
        self.assertEqual(result.invalid_rows, 1)
        self.assertEqual(result.written_rows, 0)
        self.assertEqual(load_holding_transactions(self.v2_connection), ())

    def test_v1_source_file_is_unchanged_by_dry_run_and_target_import(self) -> None:
        self._insert_v1_row()
        signature_before = (self.v1_path.stat().st_mtime_ns, self.v1_path.stat().st_size)
        dry_run_v1_holdings_import(self.v1_path, target_connection=self.v2_connection)
        import_v1_holdings(self.v1_path, target_connection=self.v2_connection)
        signature_after = (self.v1_path.stat().st_mtime_ns, self.v1_path.stat().st_size)
        self.assertEqual(signature_after, signature_before)

    def test_write_import_is_idempotent_and_preserves_note_without_current_value_fields(self) -> None:
        self._insert_v1_row(note='Imported note', dagens_verdi=999.0, pnl_nok=123.0)
        first = import_v1_holdings(self.v1_path, target_connection=self.v2_connection)
        second = import_v1_holdings(self.v1_path, target_connection=self.v2_connection)
        transactions = load_holding_transactions(self.v2_connection)
        self.assertEqual(first.would_insert, 1)
        self.assertEqual(second.would_update, 1)
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0].note, 'Imported note')
        stored_columns = {
            row['name']
            for row in self.v2_connection.execute('PRAGMA table_info(holdings_transactions_v2)')
        }
        self.assertNotIn('dagens_verdi', stored_columns)
        self.assertNotIn('pnl_nok', stored_columns)

    def test_broker_and_fallback_duplicates_remain_one_logical_target_transaction(self) -> None:
        self._insert_v1_row(transaksjons_id='broker-1', buy_date='2025-01-02')
        self._insert_v1_row(id=2, transaksjons_id='broker-1', buy_date='2025-01-03', buy_price=101.5, belop=1015.0)
        self._insert_v1_row(id=3, transaksjons_id=None, buy_date='2025-01-04', buy_price=100.0, belop=1000.0)
        self._insert_v1_row(id=4, transaksjons_id=None, buy_date='2025-01-04', buy_price=101.5, belop=1015.0)
        result = import_v1_holdings(self.v1_path, target_connection=self.v2_connection)
        self.assertEqual(result.duplicate_broker_identity_rows, 1)
        self.assertEqual(result.duplicate_fallback_identity_rows, 1)
        self.assertEqual(len(load_holding_transactions(self.v2_connection)), 2)

    def test_imported_records_feed_h2a_without_semantic_mutation(self) -> None:
        self._insert_v1_row(id=1, transaksjons_id='buy-1', transaksjonstype='KJØPT', shares=10.0, buy_price=100.0, belop=1000.0, buy_date='2025-01-02')
        self._insert_v1_row(id=2, transaksjons_id='sell-1', transaksjonstype='SALG', shares=-4.0, buy_price=120.0, belop=480.0, buy_date='2025-01-03')
        import_v1_holdings(self.v1_path, target_connection=self.v2_connection)
        position = reconstruct_position(record.transaction for record in load_holding_transactions(self.v2_connection))
        self.assertTrue(position.open)
        self.assertEqual(position.open_quantity, 6.0)
        self.assertEqual(position.remaining_cost_basis, 600.0)

    def test_import_requires_explicitly_initialized_target_schema(self) -> None:
        self._insert_v1_row()
        with sqlite3.connect(Path(self.temp_dir.name) / 'uninitialized_target.sqlite') as connection:
            with self.assertRaisesRegex(ValueError, 'initialized explicitly'):
                import_v1_holdings(self.v1_path, target_connection=connection)
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        self.assertEqual(tables, [])

    def _create_v1_schema(self) -> None:
        with sqlite3.connect(self.v1_path) as connection:
            connection.execute(
                '''
                CREATE TABLE holdings (
                    id INTEGER PRIMARY KEY,
                    ticker TEXT,
                    ticker_source TEXT,
                    fullname TEXT,
                    isin TEXT,
                    Verdipapir TEXT,
                    buy_date TEXT,
                    settlement_date TEXT,
                    shares REAL,
                    buy_price REAL,
                    note TEXT,
                    transaksjonstype TEXT,
                    valuta TEXT,
                    belop REAL,
                    kurtasje REAL,
                    nordnet_resultat REAL,
                    dagens_verdi REAL,
                    pnl_nok REAL,
                    transaksjons_id TEXT,
                    transaction_key TEXT,
                    cost_basis_missing INTEGER
                )
                '''
            )

    def _insert_v1_row(self, **overrides: object) -> None:
        payload: dict[str, object] = {
            'id': 1,
            'ticker': 'SYNTH.OL',
            'ticker_source': 'synthetic',
            'fullname': 'Synthetic Holding',
            'isin': 'NO-SYNTH-01',
            'Verdipapir': 'Synthetic Holding',
            'buy_date': '2025-01-02',
            'settlement_date': '2025-01-06',
            'shares': 10.0,
            'buy_price': 100.0,
            'note': None,
            'transaksjonstype': 'KJØPT',
            'valuta': 'NOK',
            'belop': 1000.0,
            'kurtasje': 5.0,
            'nordnet_resultat': None,
            'dagens_verdi': None,
            'pnl_nok': None,
            'transaksjons_id': None,
            'transaction_key': None,
            'cost_basis_missing': 0,
        }
        payload.update(overrides)
        if payload['transaction_key'] is None and payload['buy_date'] is not None:
            payload['transaction_key'] = fallback_transaction_key(
                HoldingTransaction(
                    transaction_type=str(payload['transaksjonstype']),
                    shares=float(payload['shares']),
                    price=float(payload['buy_price']) if payload['buy_price'] is not None else None,
                    amount=float(payload['belop']) if payload['belop'] is not None else None,
                    trade_date=str(payload['buy_date']),
                    settlement_date=str(payload['settlement_date']) if payload['settlement_date'] is not None else None,
                    isin=str(payload['isin']) if payload['isin'] is not None else None,
                    ticker=str(payload['ticker']) if payload['ticker'] is not None else None,
                    instrument_name=str(payload['Verdipapir']) if payload['Verdipapir'] is not None else None,
                    currency=str(payload['valuta']) if payload['valuta'] is not None else None,
                    nordnet_transaction_id=str(payload['transaksjons_id']) if payload['transaksjons_id'] is not None else None,
                )
            )
        columns = tuple(payload)
        placeholders = ', '.join('?' for _ in columns)
        with sqlite3.connect(self.v1_path) as connection:
            connection.execute(
                f"INSERT INTO holdings ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(payload[column] for column in columns),
            )


if __name__ == '__main__':
    unittest.main()
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tradetool.holdings import (
    DEFAULT_NORWAY_BENCHMARK_ID,
    HoldingSettings,
    HoldingSignalInputs,
    HoldingSignalRules,
    HoldingTransaction,
    evaluate_holding_signal,
    fallback_transaction_key,
    import_v1_holdings,
    import_v1_holdings_settings,
    initialize_holdings_schema,
    load_holding_settings,
    load_holding_transactions,
    reconstruct_position,
    transaction_identity,
)


FIXTURE_PATH = Path('tests/fixtures/holdings_v1_parity/holdings_v1_parity.json')


class HoldingsEndToEndParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        fixture = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
        self.transaction_scenarios = {scenario['id']: scenario for scenario in fixture['transaction_scenarios']}
        self.signal_scenarios = {scenario['id']: scenario for scenario in fixture['signal_scenarios']}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_frozen_transaction_scenarios_round_trip_through_v1_import_and_v2_storage(self) -> None:
        for scenario_id, scenario in self.transaction_scenarios.items():
            with self.subTest(scenario_id=scenario_id):
                source_transactions = self._source_transactions(scenario_id, scenario['transactions'])
                with self._target_connection() as target_connection:
                    v1_path = self._create_v1_source(scenario_id, source_transactions)
                    result = import_v1_holdings(v1_path, target_connection=target_connection)
                    stored_records = load_holding_transactions(target_connection)

                self.assertEqual(result.source_rows, len(source_transactions))
                self.assertEqual(result.invalid_rows, 0)
                self.assertEqual(
                    [record.transaction.trade_date for record in stored_records],
                    sorted(record.transaction.trade_date for record in stored_records),
                )
                self._assert_storage_identity_and_values(scenario, source_transactions, stored_records)
                self._assert_position_matches_expected(
                    reconstruct_position(record.transaction for record in stored_records),
                    scenario['expected'],
                )

    def test_frozen_signal_scenarios_follow_v2_settings_storage_and_h2b(self) -> None:
        normal_purchase = self.transaction_scenarios['normal_purchase']
        source_transactions = self._source_transactions('signal_position', normal_purchase['transactions'])
        with self._target_connection() as target_connection:
            v1_path = self._create_v1_source(
                'signal_position',
                source_transactions,
                settings={'sell_drop_from_peak': 'true'},
            )
            import_v1_holdings(v1_path, target_connection=target_connection)
            import_v1_holdings_settings(v1_path, target_connection=target_connection)
            position = reconstruct_position(record.transaction for record in load_holding_transactions(target_connection))
            settings = load_holding_settings(target_connection)

        self._assert_position_matches_expected(position, normal_purchase['expected'])
        self.assertEqual(settings, HoldingSettings(sell_drop_from_peak=True))
        rules = HoldingSignalRules(
            sell_if_rs_weak=settings.sell_rs_weak,
            sell_if_below_cost_basis=settings.sell_below_cost_basis,
            sell_if_drop_from_peak=settings.sell_drop_from_peak,
            sell_fast_sma_days=settings.sell_fast_sma_days,
            atr_multiplier=settings.atr_multiplier,
            sell_rs_threshold=settings.rs_threshold,
        )
        for scenario_id, scenario in self.signal_scenarios.items():
            with self.subTest(scenario_id=scenario_id):
                evaluation = evaluate_holding_signal(HoldingSignalInputs(**scenario['inputs']), rules=rules)
                self.assertEqual(evaluation.action, scenario['expected']['signal'])
                self.assertEqual(evaluation.reasons, tuple(scenario['expected']['reasons']))

    def test_frozen_v1_settings_map_to_typed_v2_defaults_without_benchmark_migration(self) -> None:
        source_transactions = self._source_transactions(
            'settings_position',
            self.transaction_scenarios['normal_purchase']['transactions'],
        )
        with self._target_connection() as target_connection:
            v1_path = self._create_v1_source(
                'settings_position',
                source_transactions,
                settings={
                    'period_label': '5 år',
                    'rs_months': '6',
                    'sell_rs_weak': 'true',
                    'sell_below_cost_basis': 'true',
                    'sell_drop_from_peak': 'true',
                    'holdings_sell_sma_days': '50',
                },
            )
            result = import_v1_holdings_settings(v1_path, target_connection=target_connection)
            settings = load_holding_settings(target_connection)

        expected = HoldingSettings(
            period_label='5 år',
            rs_months=6,
            sell_rs_weak=True,
            sell_below_cost_basis=True,
            sell_drop_from_peak=True,
            sell_fast_sma_days=50,
        )
        self.assertEqual(result.recognized_setting_keys_missing, ('holdings_atr_multiplier', 'sell_rs_threshold'))
        self.assertEqual(settings, expected)
        self.assertEqual(settings.atr_multiplier, 2.5)
        self.assertEqual(settings.rs_threshold, 1.05)
        self.assertEqual(settings.norway_benchmark_id, DEFAULT_NORWAY_BENCHMARK_ID)

    def _source_transactions(
        self,
        scenario_id: str,
        transactions: list[dict[str, object]],
    ) -> tuple[HoldingTransaction, ...]:
        return tuple(
            HoldingTransaction(
                transaction_type=str(transaction['type']),
                shares=float(transaction.get('shares', 0.0)),
                price=_optional_float(transaction.get('price')),
                amount=_optional_float(transaction.get('amount')),
                trade_date=str(transaction.get('trade_date') or f'2025-01-{index + 2:02d}'),
                settlement_date=str(transaction.get('settlement_date') or f'2025-01-{index + 5:02d}'),
                isin=str(transaction.get('isin') or f'NO-{scenario_id.upper()}'),
                ticker='SYNTH.OL',
                instrument_name='Synthetic Holding',
                currency=str(transaction.get('currency') or 'NOK'),
                nordnet_transaction_id=_optional_text(transaction.get('nordnet_transaction_id')),
            )
            for index, transaction in enumerate(transactions)
        )

    def _create_v1_source(
        self,
        source_name: str,
        transactions: tuple[HoldingTransaction, ...],
        *,
        settings: dict[str, str] | None = None,
    ) -> Path:
        v1_path = Path(self.temp_dir.name) / f'{source_name}.sqlite'
        with sqlite3.connect(v1_path) as connection:
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
            connection.execute(
                '''
                CREATE TABLE app_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                '''
            )
            connection.executemany(
                '''
                INSERT INTO holdings (
                    id, ticker, ticker_source, fullname, isin, Verdipapir, buy_date,
                    settlement_date, shares, buy_price, note, transaksjonstype, valuta,
                    belop, kurtasje, nordnet_resultat, dagens_verdi, pnl_nok,
                    transaksjons_id, transaction_key, cost_basis_missing
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    (
                        index,
                        transaction.ticker,
                        'synthetic',
                        transaction.instrument_name,
                        transaction.isin,
                        transaction.instrument_name,
                        transaction.trade_date,
                        transaction.settlement_date,
                        transaction.shares,
                        transaction.price,
                        None,
                        transaction.transaction_type,
                        transaction.currency,
                        transaction.amount,
                        None,
                        None,
                        None,
                        None,
                        transaction.nordnet_transaction_id,
                        fallback_transaction_key(transaction),
                        0,
                    )
                    for index, transaction in enumerate(transactions, start=1)
                ),
            )
            connection.executemany(
                'INSERT INTO app_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)',
                ((key, value, '2025-01-01T00:00:00Z') for key, value in (settings or {}).items()),
            )
        return v1_path

    def _assert_storage_identity_and_values(
        self,
        scenario: dict[str, object],
        source_transactions: tuple[HoldingTransaction, ...],
        stored_records: tuple[object, ...],
    ) -> None:
        expected = scenario['expected']
        self.assertEqual(len(stored_records), expected.get('stored_transaction_count', len(source_transactions)))
        if expected.get('identity_method'):
            self.assertEqual(transaction_identity(stored_records[0].transaction).method, expected['identity_method'])
        if scenario['id'] == 'duplicate_nordnet_transaction_id':
            self.assertEqual(stored_records[0].transaction.price, expected['latest_price'])
            self.assertEqual(stored_records[0].transaction.amount, expected['latest_amount'])
            return
        if scenario['id'] == 'duplicate_fallback_transaction_key':
            self.assertEqual(fallback_transaction_key(source_transactions[0]), fallback_transaction_key(source_transactions[1]))
            return
        for record, source_transaction in zip(stored_records, source_transactions, strict=True):
            self.assertEqual(record.transaction, source_transaction)
            self.assertEqual(record.fallback_key, fallback_transaction_key(source_transaction))

    def _assert_position_matches_expected(self, position: object, expected: dict[str, object]) -> None:
        if 'open' not in expected:
            return
        self.assertEqual(position.open, expected['open'])
        self.assertAlmostEqual(position.open_quantity, expected['open_quantity'])
        self.assertAlmostEqual(position.remaining_cost_basis, expected['remaining_cost_basis'])
        self.assertEqual(position.cost_basis_status, expected['cost_basis_status'])
        if expected['gav'] is None:
            self.assertIsNone(position.gav)
        else:
            self.assertAlmostEqual(position.gav, expected['gav'])
        if 'priced_open_quantity' in expected:
            self.assertAlmostEqual(position.priced_open_quantity, expected['priced_open_quantity'])
        if 'reporting_cashflows' in expected:
            self.assertEqual(
                [(cashflow.transaction_type, cashflow.amount) for cashflow in position.cashflows],
                [(cashflow['type'], cashflow['amount']) for cashflow in expected['reporting_cashflows']],
            )

    def _target_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(':memory:')
        connection.row_factory = sqlite3.Row
        initialize_holdings_schema(connection)
        return connection


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None else None


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


if __name__ == '__main__':
    unittest.main()
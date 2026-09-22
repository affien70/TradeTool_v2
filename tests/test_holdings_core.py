from __future__ import annotations

import json
import unittest
from pathlib import Path

from tradetool.holdings import (
    HoldingTransaction,
    canonicalize_transactions,
    fallback_transaction_key,
    reconstruct_position,
    transaction_identity,
)


FIXTURE_PATH = Path('tests/fixtures/holdings_v1_parity/holdings_v1_parity.json')


class HoldingsCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
        self.scenarios = {scenario['id']: scenario for scenario in fixture['transaction_scenarios']}

    def test_position_reconstruction_matches_h1_purchase_sale_and_transfer_cases(self) -> None:
        scenario_ids = (
            'normal_purchase',
            'multiple_purchases_same_ticker',
            'partial_sale',
            'complete_sale',
            'transferred_shares_unknown_cost',
            'transferred_and_priced_shares_partial_cost',
        )
        for scenario_id in scenario_ids:
            with self.subTest(scenario_id=scenario_id):
                scenario = self.scenarios[scenario_id]
                position = reconstruct_position(_transactions_from_fixture(scenario['transactions']))
                self._assert_position_matches_expected(position, scenario['expected'])

    def test_dividend_and_fee_are_cashflows_without_position_effect(self) -> None:
        scenario = self.scenarios['dividend_and_fee_do_not_change_open_position']
        position = reconstruct_position(_transactions_from_fixture(scenario['transactions']))
        self._assert_position_matches_expected(position, scenario['expected'])
        self.assertEqual(
            [(cashflow.transaction_type, cashflow.amount) for cashflow in position.cashflows],
            [(cashflow['type'], cashflow['amount']) for cashflow in scenario['expected']['reporting_cashflows']],
        )

    def test_nordnet_transaction_id_takes_identity_priority_and_replaces_duplicate(self) -> None:
        scenario = self.scenarios['duplicate_nordnet_transaction_id']
        canonical_transactions = canonicalize_transactions(_transactions_from_fixture(scenario['transactions']))
        expected = scenario['expected']
        self.assertEqual(len(canonical_transactions), expected['stored_transaction_count'])
        self.assertEqual(canonical_transactions[0].identity.method, expected['identity_method'])
        self.assertEqual(canonical_transactions[0].transaction.price, expected['latest_price'])
        self.assertEqual(canonical_transactions[0].transaction.amount, expected['latest_amount'])

    def test_fallback_transaction_key_is_deterministic_and_ignores_price_and_amount(self) -> None:
        scenario = self.scenarios['duplicate_fallback_transaction_key']
        first_transaction, second_transaction = _transactions_from_fixture(scenario['transactions'])
        self.assertEqual(fallback_transaction_key(first_transaction), fallback_transaction_key(second_transaction))
        self.assertEqual(
            transaction_identity(first_transaction).method,
            scenario['expected']['identity_method'],
        )
        self.assertEqual(
            len(canonicalize_transactions((first_transaction, second_transaction))),
            scenario['expected']['stored_transaction_count'],
        )

    def test_reconstruction_orders_dated_transactions_chronologically(self) -> None:
        later_sale = HoldingTransaction(
            transaction_type='SALG',
            shares=4.0,
            amount=480.0,
            trade_date='2025-01-03',
            settlement_date='2025-01-06',
        )
        earlier_purchase = HoldingTransaction(
            transaction_type='KJ\u00d8PT',
            shares=10.0,
            amount=1000.0,
            trade_date='2025-01-02',
            settlement_date='2025-01-05',
        )
        position = reconstruct_position((later_sale, earlier_purchase))
        self.assertTrue(position.open)
        self.assertEqual(position.open_quantity, 6.0)
        self.assertEqual(position.remaining_cost_basis, 600.0)

    def _assert_position_matches_expected(self, position: object, expected: dict[str, object]) -> None:
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


def _transactions_from_fixture(transactions: list[dict[str, object]]) -> tuple[HoldingTransaction, ...]:
    return tuple(
        HoldingTransaction(
            transaction_type=str(transaction['type']),
            shares=float(transaction.get('shares', 0.0)),
            price=_optional_float(transaction.get('price')),
            amount=_optional_float(transaction.get('amount')),
            trade_date=_optional_text(transaction.get('trade_date')),
            settlement_date=_optional_text(transaction.get('settlement_date')),
            isin=_optional_text(transaction.get('isin')),
            ticker=_optional_text(transaction.get('ticker')),
            instrument_name=_optional_text(transaction.get('instrument_name')),
            currency=_optional_text(transaction.get('currency')),
            nordnet_transaction_id=_optional_text(transaction.get('nordnet_transaction_id')),
        )
        for transaction in transactions
    )


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None else None


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


if __name__ == '__main__':
    unittest.main()
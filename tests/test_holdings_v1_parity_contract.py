from __future__ import annotations

import json
import unittest
from pathlib import Path


FIXTURE_PATH = Path('tests/fixtures/holdings_v1_parity/holdings_v1_parity.json')
CONTRACT_PATH = Path('docs/v2/HOLDINGS_V1_PARITY_CONTRACT.md')
REQUIRED_TRANSACTION_CASES = {
    'normal_purchase',
    'multiple_purchases_same_ticker',
    'partial_sale',
    'complete_sale',
    'transferred_shares_unknown_cost',
    'transferred_and_priced_shares_partial_cost',
    'duplicate_nordnet_transaction_id',
    'duplicate_fallback_transaction_key',
    'dividend_and_fee_do_not_change_open_position',
}
REQUIRED_SIGNAL_CASES = {
    'healthy_position',
    'isolated_fast_sma_breach',
    'fast_sma_with_weak_rs',
    'fast_sma_with_negative_1m_momentum',
    'below_sma200',
    'cost_stop_at_ten_percent',
    'isolated_atr_trailing_stop_breach',
    'atr_breach_with_weak_rs',
    'atr_breach_with_negative_1m_momentum',
}


class HoldingsV1ParityContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))

    def test_fixture_is_synthetic_and_keeps_benchmark_migration_open(self) -> None:
        self.assertTrue(self.fixture['source_is_synthetic'])
        self.assertTrue(self.fixture['v1_reference_only'])
        self.assertIn('OPEN DECISION', self.fixture['benchmark_migration'])
        self.assertNotIn('portfolio.sqlite', json.dumps(self.fixture).lower())

    def test_transaction_fixture_contains_required_v1_cases(self) -> None:
        scenarios = {scenario['id']: scenario for scenario in self.fixture['transaction_scenarios']}
        self.assertTrue(REQUIRED_TRANSACTION_CASES.issubset(scenarios))
        self.assertEqual(self.fixture['transaction_identity']['broker_transaction_id_priority'], 'Nordnet transaction ID when present')
        self.assertEqual(
            self.fixture['transaction_identity']['fallback_key_fields'],
            ['trade_date', 'settlement_date', 'isin_or_ticker_or_instrument_name', 'transaction_type', 'normalized_shares', 'currency'],
        )
        self.assertEqual(self.fixture['transaction_identity']['fallback_key_ignores'], ['price', 'amount'])

    def test_open_position_expectations_are_internally_coherent(self) -> None:
        scenarios = {scenario['id']: scenario for scenario in self.fixture['transaction_scenarios']}
        for scenario in scenarios.values():
            expected = scenario['expected']
            if 'open' not in expected:
                continue
            self.assertGreaterEqual(expected['open_quantity'], 0.0, scenario['id'])
            self.assertGreaterEqual(expected['remaining_cost_basis'], 0.0, scenario['id'])
            if expected['open']:
                self.assertGreater(expected['open_quantity'], 0.0, scenario['id'])
                self.assertIn(expected['cost_basis_status'], self.fixture['cost_basis_statuses'], scenario['id'])
            else:
                self.assertEqual(expected['open_quantity'], 0.0, scenario['id'])
                self.assertEqual(expected['remaining_cost_basis'], 0.0, scenario['id'])
                self.assertIsNone(expected['gav'], scenario['id'])
            if expected.get('priced_open_quantity') is not None:
                self.assertLessEqual(expected['priced_open_quantity'], expected['open_quantity'], scenario['id'])
            if expected['cost_basis_status'] == 'known':
                self.assertAlmostEqual(expected['gav'], expected['remaining_cost_basis'] / expected['open_quantity'], places=8)
            if expected['cost_basis_status'] == 'partially_unknown':
                self.assertAlmostEqual(expected['gav'], expected['remaining_cost_basis'] / expected['priced_open_quantity'], places=8)
            if expected['cost_basis_status'] == 'unknown':
                self.assertIsNone(expected['gav'], scenario['id'])

    def test_signal_fixture_contains_only_v1_supported_actions_and_reasons(self) -> None:
        scenarios = {scenario['id']: scenario for scenario in self.fixture['signal_scenarios']}
        self.assertTrue(REQUIRED_SIGNAL_CASES.issubset(scenarios))
        for scenario in scenarios.values():
            expected = scenario['expected']
            self.assertIn(expected['signal'], {'HOLD', 'FØLG MED', 'SELL'})
            self.assertIsInstance(expected['reasons'], list)
        self.assertEqual(scenarios['healthy_position']['expected'], {'signal': 'HOLD', 'reasons': []})
        self.assertEqual(scenarios['below_sma200']['expected']['reasons'], ['Langsiktig trend brutt: kurs under SMA200'])
        self.assertEqual(scenarios['cost_stop_at_ten_percent']['expected']['reasons'], ['10% under kostpris'])

    def test_contract_document_records_reference_boundary_and_open_decision(self) -> None:
        contract = CONTRACT_PATH.read_text(encoding='utf-8')
        self.assertIn('V1 is reference only', contract)
        self.assertIn('must not become a runtime dependency', contract)
        self.assertIn('OPEN DECISION', contract)
        self.assertIn('^OSEAX', contract)


if __name__ == '__main__':
    unittest.main()
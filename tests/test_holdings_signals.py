from __future__ import annotations

import json
import unittest
from pathlib import Path

from tradetool.holdings import HoldingSignalInputs, HoldingSignalRules, evaluate_holding_signal


FIXTURE_PATH = Path('tests/fixtures/holdings_v1_parity/holdings_v1_parity.json')
ACTIVE_H1_RULES = HoldingSignalRules(sell_if_drop_from_peak=True)


class HoldingsSignalTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
        self.scenarios = {scenario['id']: scenario for scenario in fixture['signal_scenarios']}

    def test_h1_signal_scenarios_match_actions_and_reasons(self) -> None:
        for scenario_id, scenario in self.scenarios.items():
            with self.subTest(scenario_id=scenario_id):
                evaluation = evaluate_holding_signal(
                    HoldingSignalInputs(**scenario['inputs']),
                    rules=ACTIVE_H1_RULES,
                )
                self.assertEqual(evaluation.action, scenario['expected']['signal'])
                self.assertEqual(evaluation.reasons, tuple(scenario['expected']['reasons']))

    def test_v1_precedence_keeps_cost_and_long_sma_before_fast_sma_and_atr(self) -> None:
        evaluation = evaluate_holding_signal(
            HoldingSignalInputs(
                below_fast_sma=True,
                weak_rs=True,
                below_cost_basis=True,
                drop_from_peak=True,
                below_long_sma=True,
                short_term_return=-0.01,
                fast_sma_slope_positive=False,
            ),
            rules=ACTIVE_H1_RULES,
        )
        self.assertEqual(evaluation.action, 'SELL')
        self.assertEqual(
            evaluation.reasons,
            (
                '10% under kostpris',
                'Langsiktig trend brutt: kurs under SMA200',
                'Kurs under SMA100',
                'ATR-stop brutt',
                'RS < 1.05',
                '1m momentum er negativt',
                'SMA100-trend er ikke stigende',
                'ATR-stop brutt (2.5x ATR)',
            ),
        )
        self.assertEqual(evaluation.primary_reason, '10% under kostpris')
        self.assertEqual(evaluation.supporting_reasons, evaluation.reasons[1:])

    def test_rule_toggles_disable_only_their_corresponding_breach(self) -> None:
        evaluation = evaluate_holding_signal(
            HoldingSignalInputs(
                below_fast_sma=False,
                weak_rs=False,
                below_cost_basis=True,
                drop_from_peak=True,
                short_term_return=0.02,
                fast_sma_slope_positive=True,
            ),
            rules=HoldingSignalRules(sell_if_below_cost_basis=False, sell_if_drop_from_peak=False),
        )
        self.assertEqual(evaluation.action, 'HOLD')
        self.assertEqual(evaluation.reasons, ())


if __name__ == '__main__':
    unittest.main()
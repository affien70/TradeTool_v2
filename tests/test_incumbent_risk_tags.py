from __future__ import annotations

import unittest
from pathlib import Path

from tradetool.explanation.incumbent_risk_tags import build_incumbent_risk_tags


class IncumbentRiskTagsTests(unittest.TestCase):
    def test_low_risk_when_no_flags_are_present(self) -> None:
        result = build_incumbent_risk_tags(
            {
                'average_traded_value_20': 2_000_000.0,
                'volatility_63': 0.02,
                'drawdown_252': -0.10,
                'above_sma200': True,
                'return_3m': 0.08,
                'relative_strength_3m': 0.03,
                'distance_to_sma200': 0.15,
            }
        )
        self.assertEqual(result.risk_level, 'LOW')
        self.assertEqual(result.risk_tags, ())
        self.assertIn('Lav risiko', result.risk_explanation_no)

    def test_medium_risk_for_soft_warning_tags(self) -> None:
        result = build_incumbent_risk_tags(
            {
                'average_traded_value_20': 500_000.0,
                'volatility_63': 0.02,
                'drawdown_252': -0.10,
                'above_sma200': True,
                'return_3m': -0.01,
                'relative_strength_3m': -0.02,
                'distance_to_sma200': 0.15,
            }
        )
        self.assertEqual(result.risk_level, 'MEDIUM')
        self.assertEqual(result.risk_tags, ('low_liquidity', 'negative_3m_return', 'negative_3m_rs'))
        self.assertIn('Middels risiko', result.risk_explanation_no)

    def test_high_risk_for_material_risk_tags(self) -> None:
        result = build_incumbent_risk_tags(
            {
                'average_traded_value_20': 100_000.0,
                'volatility_63': 0.08,
                'drawdown_252': -0.50,
                'above_sma200': False,
                'return_3m': 0.01,
                'relative_strength_3m': 0.01,
                'distance_to_sma200': 0.80,
            }
        )
        self.assertEqual(result.risk_level, 'HIGH')
        self.assertEqual(
            result.risk_tags,
            ('very_low_liquidity', 'high_volatility', 'deep_drawdown', 'below_sma200', 'extreme_sma200_stretch'),
        )
        self.assertIn('Høy risiko', result.risk_explanation_no)

    def test_missing_metrics_are_tagged_deterministically(self) -> None:
        result = build_incumbent_risk_tags({'average_traded_value_20': 1_000_000.0})
        self.assertEqual(result.risk_level, 'MEDIUM')
        self.assertEqual(result.risk_tags, ('missing_risk_metric',))
        self.assertIn('Mangler risikomålinger', result.risk_explanation_no)

    def test_module_has_no_ui_ml_or_holdings_dependency(self) -> None:
        source = Path('src/tradetool/explanation/incumbent_risk_tags.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('streamlit', source)
        self.assertNotIn('ml_runs', source)
        self.assertNotIn('tradetool.holdings', source)


if __name__ == '__main__':
    unittest.main()

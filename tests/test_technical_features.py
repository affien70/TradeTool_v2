from __future__ import annotations

from math import nan
from pathlib import Path
import unittest

from tradetool.features import (
    AtrTrailingStop,
    atr_trailing_stop,
    average_true_range,
    fast_sma_slope_positive,
    holdings_relative_strength,
    post_entry_peak,
    simple_moving_average,
    trailing_stop_breached,
)


class TechnicalFeatureTests(unittest.TestCase):
    def test_holdings_relative_strength_matches_v1_ratio_with_independent_series(self) -> None:
        asset = [100.0] * 130 + [120.0]
        benchmark = [100.0] * 130 + [110.0]
        result = holdings_relative_strength(asset, benchmark, months=6)
        self.assertAlmostEqual(result, (120.0 / 100.0) / (110.0 / 100.0))
        self.assertGreater(result, 1.05)

    def test_holdings_relative_strength_requires_v1_days_plus_five_and_ignores_invalid_values(self) -> None:
        self.assertIsNone(holdings_relative_strength([100.0] * 130, [100.0] * 130, months=6))
        asset = [nan, *([100.0] * 131)]
        benchmark = [nan, *([100.0] * 131)]
        self.assertAlmostEqual(holdings_relative_strength(asset, benchmark, months=6), 1.0)
        self.assertIsNone(holdings_relative_strength([100.0] * 131, [0.0] * 130 + [100.0], months=6))

    def test_simple_moving_average_supports_arbitrary_windows_and_missing_history(self) -> None:
        self.assertEqual(simple_moving_average([1.0, 2.0, 3.0, 4.0], window=3), 3.0)
        self.assertIsNone(simple_moving_average([1.0, 2.0], window=3))
        self.assertIsNone(simple_moving_average([1.0, nan, 3.0], window=3))
        with self.assertRaisesRegex(ValueError, 'positive'):
            simple_moving_average([1.0], window=0)

    def test_fast_sma_slope_matches_v1_twentieth_valid_sma_comparison(self) -> None:
        rising = list(range(1, 25))
        falling = list(range(24, 0, -1))
        flat = [10.0] * 24
        self.assertTrue(fast_sma_slope_positive(rising, window=3))
        self.assertFalse(fast_sma_slope_positive(falling, window=3))
        self.assertFalse(fast_sma_slope_positive(flat, window=3))
        self.assertIsNone(fast_sma_slope_positive(list(range(1, 22)), window=3))

    def test_average_true_range_matches_v1_true_range_and_simple_rolling_mean(self) -> None:
        highs = [11.0, 13.0, 14.0]
        lows = [9.0, 10.0, 11.0]
        closes = [10.0, 12.0, 12.0]
        self.assertAlmostEqual(average_true_range(highs, lows, closes, window=3), (2.0 + 3.0 + 3.0) / 3.0)
        self.assertIsNone(average_true_range(highs, lows, closes, window=14))
        self.assertIsNone(average_true_range(highs, lows[:-1], closes, window=2))
        self.assertEqual(average_true_range([11.0, nan], [9.0, 10.0], [10.0, 12.0], window=2), 1.0)
        self.assertEqual(average_true_range([11.0, 13.0], [9.0, 11.0], [10.0, nan], window=2), 2.5)

    def test_post_entry_peak_and_trailing_stop_match_v1_activation_stop_and_breach(self) -> None:
        self.assertEqual(post_entry_peak([100.0, nan, 112.0, 110.0]), 112.0)
        inactive = atr_trailing_stop(close=104.0, entry_price=100.0, peak_price=104.0, atr=2.0)
        self.assertEqual(inactive, AtrTrailingStop(2.0 / 104.0, 0.10, None, False))
        active = atr_trailing_stop(close=100.0, entry_price=100.0, peak_price=120.0, atr=2.0)
        self.assertEqual(active, AtrTrailingStop(2.0 / 120.0, 0.10, 108.0, True))
        self.assertTrue(trailing_stop_breached(close=108.0, trailing_stop=active))
        self.assertFalse(trailing_stop_breached(close=108.1, trailing_stop=active))

    def test_trailing_stop_uses_atr_multiplier_and_rejects_unknown_cost_context(self) -> None:
        amplified = atr_trailing_stop(close=115.0, entry_price=100.0, peak_price=120.0, atr=8.0, atr_multiplier=2.5)
        self.assertEqual(amplified, AtrTrailingStop(8.0 / 120.0, 1.0 / 6.0, 100.0, True))
        for invalid_entry in (None, 0.0, nan):
            with self.subTest(invalid_entry=invalid_entry):
                self.assertEqual(
                    atr_trailing_stop(close=100.0, entry_price=invalid_entry, peak_price=120.0, atr=2.0),
                    AtrTrailingStop(None, None, None, False),
                )

    def test_technical_module_has_no_runtime_or_ui_dependencies(self) -> None:
        source = Path('src/tradetool/features/technical.py').read_text(encoding='utf-8').lower()
        for forbidden in ('sqlite', 'streamlit', 'tradetool.holdings', 'tradetool.ui', 'pages.'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
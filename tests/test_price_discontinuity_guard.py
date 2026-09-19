from __future__ import annotations

import unittest
from datetime import date

from tradetool.data.price_history_v2 import PriceHistoryV2Record
from tradetool.diagnostics.price_discontinuity_guard import (
    detect_price_discontinuities,
    holding_window_crosses_discontinuity,
    index_price_discontinuities,
)


def _row(day: int, raw: float, adjusted: float) -> PriceHistoryV2Record:
    return PriceHistoryV2Record('TEST.OL', date(2025, 1, day), raw, raw, raw, adjusted, raw, adjusted, 1.0, 'test')


class PriceDiscontinuityGuardTests(unittest.TestCase):
    def test_detects_raw_or_adjusted_five_times_discontinuities(self) -> None:
        events = detect_price_discontinuities({'TEST.OL': (_row(2, 1.0, 1.0), _row(3, 5.0, 5.0), _row(4, 1.0, 1.0))})
        self.assertEqual([(event.previous_date, event.current_date) for event in events], [(date(2025, 1, 2), date(2025, 1, 3)), (date(2025, 1, 3), date(2025, 1, 4))])

    def test_window_crossing_is_horizon_specific(self) -> None:
        events = index_price_discontinuities(detect_price_discontinuities({'TEST.OL': (_row(2, 1.0, 1.0), _row(3, 5.0, 5.0))}))
        self.assertFalse(holding_window_crosses_discontinuity(ticker='TEST.OL', entry_date=date(2025, 1, 2), exit_date=date(2025, 1, 2), events_by_ticker=events))
        self.assertTrue(holding_window_crosses_discontinuity(ticker='TEST.OL', entry_date=date(2025, 1, 2), exit_date=date(2025, 1, 3), events_by_ticker=events))
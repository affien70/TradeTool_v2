from __future__ import annotations

from datetime import date
import unittest

from tradetool.holdings import HoldingPositionRow, HoldingSettings, HoldingsPageResult, HoldingsPageSummary
from tradetool.ui.holdings import (
    build_holding_table_row,
    build_holdings_summary_display,
    page_state_message,
)


def _summary(**overrides: object) -> HoldingsPageSummary:
    values: dict[str, object] = {
        'open_position_count': 1,
        'total_current_market_value': 1_250.0,
        'total_known_unrealized_pnl_nok': 250.0,
        'market_value_position_count': 1,
        'known_pnl_position_count': 1,
        'known_cost_basis_position_count': 1,
        'partially_unknown_cost_basis_position_count': 0,
        'unknown_cost_basis_position_count': 0,
        'has_unknown_cost_basis': False,
        'hold_count': 1,
        'follow_up_count': 0,
        'sell_count': 0,
        'unavailable_count': 0,
    }
    values.update(overrides)
    return HoldingsPageSummary(**values)


def _result(*, rows: tuple[HoldingPositionRow, ...] = (), schema_ready: bool = True, summary: HoldingsPageSummary | None = None) -> HoldingsPageResult:
    return HoldingsPageResult(
        holdings_schema_ready=schema_ready,
        settings=HoldingSettings(),
        summary=summary or _summary(open_position_count=len(rows)),
        rows=rows,
        details=(),
    )


class HoldingsPresentationTests(unittest.TestCase):
    def test_known_cost_position_uses_engine_values(self) -> None:
        row = HoldingPositionRow(
            position_key='ticker:KNOWN.OL', ticker='KNOWN.OL', instrument_name='Kjent AS', quantity=10.0,
            cost_basis_status='known', gav=100.0, current_price=125.0, current_market_value=1_250.0,
            unrealized_pnl_nok=250.0, unrealized_pnl_pct=0.25, signal_action='HOLD',
            signal_reasons=('Pris over kjøpskurs.',), market_data_as_of_date=date(2026, 9, 21),
        )

        display = build_holding_table_row(row)

        self.assertEqual(display['Antall'], '10')
        self.assertEqual(display['GAV'], '100,00 kr')
        self.assertEqual(display['Urealisert P/L NOK'], '250,00 kr')
        self.assertEqual(display['Urealisert P/L %'], '25,00%')
        self.assertEqual(display['Signal'], 'HOLD')
        self.assertEqual(display['Forklaring'], 'Pris over kjøpskurs.')
        self.assertEqual(display['Markedsdato'], '21.09.2026')

    def test_partial_and_unknown_cost_basis_do_not_show_return(self) -> None:
        row = HoldingPositionRow(
            position_key='ticker:PARTIAL.OL', ticker='PARTIAL.OL', instrument_name='Delvis AS', quantity=10.0,
            cost_basis_status='partially_unknown', gav=100.0, current_price=125.0, current_market_value=1_250.0,
            unrealized_pnl_nok=None, unrealized_pnl_pct=None, signal_action='FØLG MED',
            signal_reasons=('Relativ styrke er svak.',), market_data_as_of_date=None,
        )

        display = build_holding_table_row(row)

        self.assertEqual(display['GAV'], 'Delvis kjent')
        self.assertEqual(display['Urealisert P/L NOK'], 'Delvis kjent')
        self.assertEqual(display['Urealisert P/L %'], 'Delvis kjent')
        self.assertEqual(display['Kostgrunnlag'], 'Delvis kjent')

    def test_unavailable_signal_uses_engine_reason(self) -> None:
        row = HoldingPositionRow(
            position_key='ticker:UNAVAILABLE.OL', ticker='UNAVAILABLE.OL', instrument_name='Mangler AS', quantity=1.0,
            cost_basis_status='unknown', gav=None, current_price=None, current_market_value=None,
            unrealized_pnl_nok=None, unrealized_pnl_pct=None, signal_action=None, signal_reasons=(),
            market_data_as_of_date=None, unavailable_reasons=('market_data_unavailable',),
        )

        display = build_holding_table_row(row)

        self.assertEqual(display['Signal'], 'Utilgjengelig')
        self.assertEqual(display['Forklaring'], 'Markedsdata er utilgjengelig')
        self.assertEqual(display['GAV'], 'Ukjent')

    def test_summary_marks_incomplete_cost_basis(self) -> None:
        summary = _summary(
            open_position_count=3,
            known_pnl_position_count=1,
            known_cost_basis_position_count=1,
            partially_unknown_cost_basis_position_count=1,
            unknown_cost_basis_position_count=1,
            has_unknown_cost_basis=True,
            hold_count=1,
            follow_up_count=1,
            sell_count=0,
            unavailable_count=1,
        )

        display = build_holdings_summary_display(_result(summary=summary))

        self.assertEqual(display.known_unrealized_pnl, '250,00 kr')
        self.assertIn('bare posisjoner med kjent kostgrunnlag', display.cost_basis_notice or '')
        self.assertEqual(display.signal_counts, 'HOLD: 1 | FØLG MED: 1 | SELL: 0 | Utilgjengelig: 1')

    def test_empty_and_schema_unavailable_results_have_safe_messages(self) -> None:
        self.assertEqual(page_state_message(_result()), 'Ingen åpne beholdninger å vise.')
        self.assertEqual(page_state_message(_result(schema_ready=False)), 'Beholdningslageret er ikke klart ennå.')


if __name__ == '__main__':
    unittest.main()
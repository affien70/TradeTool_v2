from __future__ import annotations

from datetime import date
from pathlib import Path
import unittest

from plotly.graph_objects import Figure

from tradetool.features.technical import AtrTrailingStop
from tradetool.holdings import (
    HoldingChartDetail,
    HoldingChartPoint,
    HoldingMarketDataResult,
    HoldingPositionDetail,
    HoldingPositionRow,
    HoldingPurchaseMarker,
    HoldingSettings,
    HoldingsPageResult,
    HoldingsPageSummary,
    PositionState,
)
from tradetool.ui.holdings import (
    build_holding_chart_figure,
    build_holdings_detail_display,
    build_holding_table_row,
    build_holdings_summary_display,
    holding_position_options,
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


def _known_row(**overrides: object) -> HoldingPositionRow:
    values: dict[str, object] = {
        'position_key': 'ticker:KNOWN.OL',
        'ticker': 'KNOWN.OL',
        'instrument_name': 'Kjent AS',
        'quantity': 10.0,
        'cost_basis_status': 'known',
        'gav': 100.0,
        'current_price': 125.0,
        'current_market_value': 1_250.0,
        'unrealized_pnl_nok': 250.0,
        'unrealized_pnl_pct': 0.25,
        'signal_action': 'HOLD',
        'signal_reasons': ('Pris over kjøpskurs.', 'Relativ styrke er positiv.'),
        'market_data_as_of_date': date(2026, 9, 21),
        'unavailable_reasons': (),
    }
    values.update(overrides)
    return HoldingPositionRow(**values)


def _detail(**overrides: object) -> HoldingPositionDetail:
    chart_values: dict[str, object] = {
        'ticker': 'KNOWN.OL',
        'benchmark_id': 'OSEBX.OL',
        'as_of_date': date(2026, 9, 21),
        'points': (
            HoldingChartPoint(date(2026, 9, 20), 120.0, 1_000.0, 115.0, 110.0, 210.0, 100.0, 100.0),
            HoldingChartPoint(date(2026, 9, 21), 125.0, 1_100.0, 116.0, 111.0, 211.0, 104.0, 100.5),
        ),
        'purchase_markers': (
            HoldingPurchaseMarker(date(2026, 1, 2), 'KJØPT', 10.0, 100.0, False),
        ),
        'current_price': 125.0,
        'fast_sma': 116.0,
        'sma200': 111.0,
        'relative_strength': 1.123,
        'short_term_return': 0.0525,
        'atr': 4.0,
        'post_entry_peak': 130.0,
        'trailing_stop': AtrTrailingStop(0.03, 0.10, 117.0, True),
        'unavailable_reasons': (),
    }
    chart_values.update(overrides.pop('chart_overrides', {}))
    chart = HoldingChartDetail(**chart_values)
    row = overrides.pop('row', _known_row())
    return HoldingPositionDetail(
        row=row,
        position=PositionState(True, 10.0, 10.0, 1_000.0, 100.0, 'known'),
        source_transactions=(),
        market_data=HoldingMarketDataResult('KNOWN.OL', 'OSEBX.OL', date(2026, 9, 21), True, None),
        signal_evaluation=None,
        chart=chart,
        **overrides,
    )


class HoldingsPresentationTests(unittest.TestCase):
    def test_known_cost_position_uses_engine_values(self) -> None:
        row = _known_row(signal_reasons=('Pris over kjøpskurs.',))

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

    def test_selector_uses_engine_position_key_and_display_label(self) -> None:
        options = holding_position_options(_result(rows=(_known_row(),)))

        self.assertEqual(options, (('ticker:KNOWN.OL', 'Kjent AS (KNOWN.OL)'),))

    def test_detail_display_formats_known_engine_values_and_ordered_reasons(self) -> None:
        display = build_holdings_detail_display(_detail())

        self.assertEqual(display.position_key, 'ticker:KNOWN.OL')
        self.assertEqual(display.gav, '100,00 kr')
        self.assertEqual(display.current_market_value, '1 250,00 kr')
        self.assertEqual(display.unrealized_pnl_pct, '25,00%')
        self.assertEqual(display.reasons, ('Pris over kjøpskurs.', 'Relativ styrke er positiv.'))
        self.assertEqual(display.relative_strength, '1,123')
        self.assertEqual(display.short_term_return, '5,25%')
        self.assertEqual(display.atr, '4,00 kr')
        self.assertEqual(display.trailing_stop, 'Aktiv: 117,00 kr')

    def test_detail_display_keeps_partial_basis_and_unavailable_state_explicit(self) -> None:
        row = _known_row(
            cost_basis_status='partially_unknown',
            gav=100.0,
            unrealized_pnl_nok=None,
            unrealized_pnl_pct=None,
            signal_action=None,
            signal_reasons=(),
            unavailable_reasons=('market_data_unavailable',),
        )
        display = build_holdings_detail_display(_detail(
            row=row,
            chart_overrides={
                'points': (),
                'relative_strength': None,
                'short_term_return': None,
                'atr': None,
                'post_entry_peak': None,
                'trailing_stop': None,
                'unavailable_reasons': ('stock_market_data_missing',),
            },
        ))

        self.assertEqual(display.gav, 'Delvis kjent')
        self.assertEqual(display.unrealized_pnl_nok, 'Delvis kjent')
        self.assertEqual(display.signal, 'Utilgjengelig')
        self.assertEqual(display.reasons, ('Markedsdata er utilgjengelig',))
        self.assertEqual(display.chart_unavailable_reasons, ('Mangler markedsdata for ticker',))

    def test_chart_uses_engine_points_markers_overlays_and_volume(self) -> None:
        figure = build_holding_chart_figure(_detail())

        self.assertIsInstance(figure, Figure)
        assert figure is not None
        traces = {trace.name: trace for trace in figure.data}
        self.assertEqual(tuple(traces['Kurs'].y), (120.0, 125.0))
        self.assertEqual(tuple(traces['SMA rask'].y), (115.0, 116.0))
        self.assertEqual(tuple(traces['SMA200'].y), (110.0, 111.0))
        self.assertEqual(tuple(traces['Kjøp'].x), (date(2026, 1, 2),))
        self.assertEqual(tuple(traces['Kjøp'].y), (100.0,))
        self.assertEqual(tuple(traces['Volum'].y), (1_000.0, 1_100.0))
        self.assertEqual(tuple(traces['KNOWN.OL (indeksert)'].y), (100.0, 104.0))
        self.assertEqual(tuple(traces['OSEBX.OL (indeksert)'].y), (100.0, 100.5))
        annotations = tuple(annotation.text for annotation in figure.layout.annotations)
        self.assertIn('Topp etter kjøp', annotations)
        self.assertIn('ATR-stopp', annotations)

    def test_chart_returns_none_without_engine_chart_points(self) -> None:
        self.assertIsNone(build_holding_chart_figure(_detail(chart_overrides={'points': ()})))

    def test_chart_helper_has_no_technical_calculation_dependency(self) -> None:
        source = Path('src/tradetool/ui/holdings.py').read_text(encoding='utf-8')
        self.assertNotIn('simple_moving_average', source)
        self.assertNotIn('holdings_relative_strength', source)
        self.assertNotIn('average_true_range', source)
        self.assertNotIn('compute_raw_features', source)


if __name__ == '__main__':
    unittest.main()
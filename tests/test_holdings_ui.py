from __future__ import annotations

from datetime import date
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

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


class _UploadedFile:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def getvalue(self) -> bytes:
        return self.content


class _HoldingsPageStreamlitStub(types.ModuleType):
    def __init__(self) -> None:
        super().__init__('streamlit')
        self.session_state: dict[str, object] = {}
        self.pressed: set[str] = set()
        self.widget_values: dict[str, object] = {}
        self.uploaded_content: bytes | None = None
        self.buttons: dict[str, bool] = {}
        self.metrics: list[tuple[str, object]] = []
        self.captions: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.successes: list[str] = []
        self.reruns = 0

    def title(self, value: str) -> None:
        self.captions.append(value)

    def subheader(self, value: str) -> None:
        self.captions.append(value)

    def caption(self, value: str) -> None:
        self.captions.append(value)

    def info(self, value: str) -> None:
        self.infos.append(value)

    def warning(self, value: str) -> None:
        self.warnings.append(value)

    def error(self, value: str) -> None:
        self.errors.append(value)

    def success(self, value: str) -> None:
        self.successes.append(value)

    def columns(self, count: int):
        return [self] * count

    def metric(self, label: str, value: object) -> None:
        self.metrics.append((label, value))

    def file_uploader(self, *args, **kwargs):
        return None if self.uploaded_content is None else _UploadedFile(self.uploaded_content)

    def button(self, label: str, *, key: str, disabled: bool = False) -> bool:
        self.buttons[key] = disabled
        return key in self.pressed and not disabled

    def selectbox(self, label: str, options, index: int = 0, *, key: str):
        return self.widget_values.get(key, options[index])

    def number_input(self, label: str, *, value, key: str, **kwargs):
        return self.widget_values.get(key, value)

    def checkbox(self, label: str, *, value: bool, key: str) -> bool:
        return bool(self.widget_values.get(key, value))

    def dataframe(self, *args, **kwargs) -> None:
        return None

    def plotly_chart(self, *args, **kwargs) -> None:
        return None

    def rerun(self) -> None:
        self.reruns += 1


def _page_status(path: Path):
    return types.SimpleNamespace(
        configured_path=path,
        exists=True,
        readable=True,
    )


def _page_result(*, schema_ready: bool, settings: HoldingSettings | None = None):
    return types.SimpleNamespace(
        holdings_schema_ready=schema_ready,
        settings=settings or HoldingSettings(),
        rows=(),
    )


def _preview(*, blocking: bool = False, new: int = 1, existing: int = 2, updates: int = 1, ignored: int = 1):
    return types.SimpleNamespace(
        parse_result=types.SimpleNamespace(source_row_count=8),
        new_row_count=new,
        existing_idempotent_count=existing,
        would_update_count=updates,
        duplicate_upload_count=0,
        ignored_non_holdings_cashflow_count=ignored,
        ignored_administrative_count=0,
        invalid_row_count=1 if blocking else 0,
        unsupported_blocker_count=0,
        conflict_count=0,
        target_schema_ready=True,
        has_blocking_errors=blocking,
    )


class HoldingsPageActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.st = _HoldingsPageStreamlitStub()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / 'holdings_ui.sqlite'
        self.database_path.touch()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _render(self, *, result, preview=None):
        build_result = Mock(return_value=result)
        dry_run = Mock(return_value=preview or _preview())
        confirmed_import = Mock(return_value=types.SimpleNamespace(
            written_row_count=2,
            preview=preview or _preview(),
        ))
        initialize_schema = Mock()
        save_settings = Mock()
        holdings_module = types.ModuleType('tradetool.holdings')
        holdings_module.HoldingSettings = HoldingSettings
        holdings_module.build_holdings_page_result = build_result
        holdings_module.dry_run_nordnet_import = dry_run
        holdings_module.import_nordnet_transactions = confirmed_import
        holdings_module.initialize_holdings_schema = initialize_schema
        holdings_module.save_holding_settings = save_settings
        runtime_module = types.ModuleType('tradetool.config.runtime_settings')
        runtime_module.inspect_app_database = lambda: _page_status(self.database_path)
        ui_module = types.ModuleType('tradetool.ui.holdings')
        ui_module.build_holding_chart_figure = lambda detail: None
        ui_module.build_holdings_detail_display = lambda detail: detail
        ui_module.build_holdings_summary_display = lambda page_result: page_result
        ui_module.build_holdings_table_rows = lambda page_result: []
        ui_module.holding_position_options = lambda page_result: ()
        ui_module.page_state_message = lambda page_result: 'Ingen åpne beholdninger å vise.'
        with patch.dict(sys.modules, {
            'streamlit': self.st,
            'tradetool.holdings': holdings_module,
            'tradetool.config.runtime_settings': runtime_module,
            'tradetool.ui.holdings': ui_module,
        }):
            path = Path('pages/beholdning.py').resolve()
            spec = importlib.util.spec_from_file_location('pages.beholdning_test_import', path)
            module = importlib.util.module_from_spec(spec)
            assert spec is not None and spec.loader is not None
            spec.loader.exec_module(module)
        return build_result, dry_run, confirmed_import, initialize_schema, save_settings

    def test_missing_schema_shows_onboarding_without_initializing(self) -> None:
        _, dry_run, confirmed_import, initialize_schema, save_settings = self._render(
            result=_page_result(schema_ready=False),
        )

        self.assertTrue(any('ikke klargjort' in message for message in self.st.infos))
        self.assertFalse(self.st.buttons['holdings_initialize_schema'])
        initialize_schema.assert_not_called()
        dry_run.assert_not_called()
        confirmed_import.assert_not_called()
        save_settings.assert_not_called()

    def test_schema_initialization_requires_the_explicit_action(self) -> None:
        self.st.pressed.add('holdings_initialize_schema')
        _, _, _, initialize_schema, _ = self._render(result=_page_result(schema_ready=False))

        initialize_schema.assert_called_once()
        self.assertEqual(self.st.reruns, 1)

    def test_uploader_passes_raw_bytes_to_dry_run_and_does_not_write_before_confirmation(self) -> None:
        self.st.uploaded_content = b'synthetic nordnet upload'
        _, dry_run, confirmed_import, _, _ = self._render(result=_page_result(schema_ready=True))

        self.assertEqual(dry_run.call_args.args[0], b'synthetic nordnet upload')
        self.assertIn(('Nye transaksjoner', 1), self.st.metrics)
        self.assertIn(('Allerede registrert', 2), self.st.metrics)
        self.assertIn(('Oppdateres', 1), self.st.metrics)
        self.assertIn(('Ignorerte kontantbevegelser', 1), self.st.metrics)
        self.assertFalse(self.st.buttons['holdings_confirm_nordnet_import'])
        confirmed_import.assert_not_called()

    def test_blocking_preview_disables_confirmed_import(self) -> None:
        self.st.uploaded_content = b'blocked upload'
        self.st.pressed.add('holdings_confirm_nordnet_import')
        _, _, confirmed_import, _, _ = self._render(
            result=_page_result(schema_ready=True),
            preview=_preview(blocking=True),
        )

        self.assertTrue(self.st.buttons['holdings_confirm_nordnet_import'])
        self.assertTrue(any('blokkert' in message for message in self.st.warnings))
        confirmed_import.assert_not_called()

    def test_ignored_rows_do_not_block_explicit_confirmed_import(self) -> None:
        self.st.uploaded_content = b'ignored rows upload'
        self.st.pressed.add('holdings_confirm_nordnet_import')
        preview = _preview(new=1, existing=0, updates=0, ignored=3)
        _, _, confirmed_import, _, _ = self._render(result=_page_result(schema_ready=True), preview=preview)

        self.assertFalse(self.st.buttons['holdings_confirm_nordnet_import'])
        self.assertEqual(confirmed_import.call_args.args[0], b'ignored rows upload')
        self.assertTrue(confirmed_import.call_args.kwargs['confirmed'])
        self.assertEqual(self.st.reruns, 1)

    def test_settings_use_typed_contract_and_osebx_default(self) -> None:
        self.st.pressed.add('holdings_save_settings')
        self.st.widget_values['holdings_sell_fast_sma_days'] = 50
        _, _, _, _, save_settings = self._render(result=_page_result(schema_ready=True, settings=HoldingSettings()))

        saved = save_settings.call_args.args[1]
        self.assertIsInstance(saved, HoldingSettings)
        self.assertEqual(saved.sell_fast_sma_days, 50)
        self.assertEqual(saved.norway_benchmark_id, 'OSEBX.OL')
        self.assertIn('Benchmark: OSEBX.OL', self.st.captions)
        self.assertEqual(self.st.reruns, 1)

    def test_ui_module_contains_no_sql_or_nordnet_business_mapping(self) -> None:
        source = Path('src/tradetool/ui/holdings.py').read_text(encoding='utf-8')
        self.assertNotIn('.execute(', source)
        self.assertNotIn('parse_nordnet_export', source)
        self.assertNotIn('import_nordnet_transactions', source)
        self.assertNotIn('_MAPPED_TRANSACTION_TYPES', source)


if __name__ == '__main__':
    unittest.main()
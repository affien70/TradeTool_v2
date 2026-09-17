from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


class _StreamlitStub:
    def title(self, *args, **kwargs) -> None:
        return None

    def write(self, *args, **kwargs) -> None:
        return None

    def info(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None

    def error(self, *args, **kwargs) -> None:
        return None

    def subheader(self, *args, **kwargs) -> None:
        return None

    def header(self, *args, **kwargs) -> None:
        return None

    def caption(self, *args, **kwargs) -> None:
        return None

    def json(self, *args, **kwargs) -> None:
        return None

    def dataframe(self, *args, **kwargs) -> None:
        return None

    def vega_lite_chart(self, *args, **kwargs) -> None:
        return None

    def cache_data(self, *args, **kwargs):
        def decorator(func):
            return func
        return decorator

    def columns(self, count):
        resolved_count = len(count) if isinstance(count, list) else int(count)
        return [self for _ in range(resolved_count)]

    def checkbox(self, *args, **kwargs) -> bool:
        return False

    def radio(self, label: str, options, index: int = 0, **kwargs):
        return options[index]

    def text_input(self, label: str, value: str = '', **kwargs) -> str:
        return value

    def text_area(self, label: str, value: str = '', **kwargs) -> str:
        return value

    def date_input(self, label: str, value=None, **kwargs):
        return value

    def number_input(self, label: str, value=0, **kwargs):
        return value

    def button(self, *args, **kwargs) -> bool:
        return False

    def expander(self, *args, **kwargs):
        return self

    def selectbox(self, label: str, options, index: int = 0, **kwargs):
        return options[index] if options else None

    def metric(self, *args, **kwargs) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class ScreenerPageTests(unittest.TestCase):
    def test_screener_page_imports_without_database_access(self) -> None:
        path = Path('pages/screener.py').resolve()
        streamlit_stub = _StreamlitStub()
        streamlit_module = types.ModuleType('streamlit')
        for name in dir(streamlit_stub):
            if not name.startswith('_'):
                setattr(streamlit_module, name, getattr(streamlit_stub, name))

        ui_module = types.ModuleType('tradetool.ui.screener')
        ui_module.CHART_PERIOD_CALENDAR_MONTHS = {'1 år': 12}
        ui_module.DEFAULT_CHART_PERIOD_LABEL = '1 år'

        def _fail(*args, **kwargs):
            raise AssertionError('database access helper should not run during import')

        ui_module.build_minimal_screener_result = _fail
        ui_module.build_incumbent_screener_ui_result = _fail
        ui_module.build_price_chart_spec = _fail
        ui_module.build_relative_strength_chart_spec = _fail
        ui_module.build_selected_ticker_chart_detail = _fail
        ui_module.build_selected_ticker_detail = _fail
        ui_module.incumbent_candidate_explanation = _fail
        ui_module.incumbent_candidate_detail_rows = _fail
        ui_module.incumbent_screener_eligible_table_rows = _fail
        ui_module.incumbent_screener_summary_rows = _fail
        ui_module.incumbent_screener_table_rows = _fail
        ui_module.incumbent_screener_ticker_options = _fail
        ui_module.resolve_incumbent_selected_ticker = _fail
        ui_module.selected_incumbent_candidate = _fail
        runtime_module = types.ModuleType('tradetool.config.runtime_settings')
        runtime_module.inspect_app_database = lambda: types.SimpleNamespace(
            configured_path=Path('/tmp/missing.sqlite'),
            configured_path_text='/tmp/missing.sqlite',
            exists=False,
            readable=False,
            price_history_v2_table_exists=False,
            row_count=None,
            latest_price_date=None,
            error=None,
        )
        previous_streamlit = sys.modules.get('streamlit')
        previous_ui = sys.modules.get('tradetool.ui.screener')
        previous_runtime = sys.modules.get('tradetool.config.runtime_settings')
        streamlit_module.session_state = {}
        sys.modules['streamlit'] = streamlit_module
        sys.modules['tradetool.ui.screener'] = ui_module
        sys.modules['tradetool.config.runtime_settings'] = runtime_module
        try:
            spec = importlib.util.spec_from_file_location('pages.screener_test_import', path)
            module = importlib.util.module_from_spec(spec)
            assert spec is not None and spec.loader is not None
            spec.loader.exec_module(module)
        finally:
            if previous_streamlit is None:
                sys.modules.pop('streamlit', None)
            else:
                sys.modules['streamlit'] = previous_streamlit
            if previous_ui is None:
                sys.modules.pop('tradetool.ui.screener', None)
            else:
                sys.modules['tradetool.ui.screener'] = previous_ui
            if previous_runtime is None:
                sys.modules.pop('tradetool.config.runtime_settings', None)
            else:
                sys.modules['tradetool.config.runtime_settings'] = previous_runtime

    def test_screener_page_has_no_automatic_db_access_on_page_load(self) -> None:
        source = Path('pages/screener.py').read_text(encoding='utf-8')
        self.assertIn("st.button('Kjør screener'", source)
        prefix = source.split("if run_clicked:", 1)[0]
        self.assertNotIn('build_incumbent_screener_ui_result(', prefix)
        self.assertIn("st.session_state.get('incumbent_screener_result')", source)

    def test_screener_page_uses_configured_database_and_dropdown_inputs(self) -> None:
        source = Path('pages/screener.py').read_text(encoding='utf-8')
        self.assertIn('inspect_app_database()', source)
        self.assertNotIn("st.text_input('DB path'", source)
        self.assertNotIn("st.text_input('Lokal kopi av SQLite-database'", source)
        self.assertNotIn("st.text_input('Universe ID'", source)
        self.assertNotIn("st.text_input('Benchmark ticker'", source)
        self.assertIn("selectbox('Univers'", source)
        self.assertIn("selectbox('Datakilde'", source)
        self.assertIn('UNIVERSE_BENCHMARKS', source)
        self.assertIn('Ingen lokal app-database funnet. Gå til Innstillinger eller bygg lokal database før screening.', source)

    def test_screener_page_keeps_screening_date_in_advanced_controls(self) -> None:
        source = Path('pages/screener.py').read_text(encoding='utf-8')
        self.assertNotIn("date_input('Dato'", source)
        self.assertIn("st.expander('Avansert'", source)
        self.assertIn("date_input(\n            'Screeningdato'", source)
        self.assertIn('Screeningdato brukes for historisk testing. I vanlig bruk velges siste tilgjengelige prisdato automatisk.', source)
        self.assertIn("date.fromisoformat(str(latest_price_date))", source)
        self.assertIn('Markedsdata til og med:', source)

    def test_screener_page_keeps_debug_details_out_of_main_screen(self) -> None:
        source = Path('pages/screener.py').read_text(encoding='utf-8')
        self.assertNotIn('st.json(', source)
        self.assertIn("st.expander('Tekniske detaljer'", source)
        self.assertIn('incumbent_screener_table_rows', source)
        self.assertIn('build_selected_ticker_chart_detail', source)
        self.assertIn("'Valgt kandidat', options=ticker_options", source)

    def test_screener_page_uses_one_selected_ticker_for_detail_and_charts(self) -> None:
        source = Path('pages/screener.py').read_text(encoding='utf-8')
        self.assertIn("detail_ticker = str(selected_row.get('ticker') or selected_ticker).strip().upper()", source)
        self.assertIn('selected_ticker=detail_ticker', source)
        self.assertIn("'selected_ticker_detail'", source)
        self.assertIn("'selected_ticker_price_chart'", source)
        self.assertIn("'selected_ticker_benchmark_chart'", source)
        self.assertIn("key=f'price_chart_{chart_detail.ticker}_{chart_detail.chart_period_label}_{chart_detail.requested_end_date}'", source)
        self.assertIn("key=f'rs_chart_{chart_detail.ticker}_{chart_detail.benchmark_ticker}_{chart_detail.chart_period_label}_{chart_detail.requested_end_date}'", source)

    def test_chart_cache_inputs_include_ticker_and_as_of_date(self) -> None:
        source = Path('pages/screener.py').read_text(encoding='utf-8')
        self.assertIn('def _cached_selected_ticker_chart_detail(', source)
        self.assertIn('selected_ticker: str', source)
        self.assertIn('benchmark_ticker: str', source)
        self.assertIn('data_source: str', source)
        self.assertIn('as_of_date_text: str', source)
        self.assertIn('chart_period_label: str', source)

    def test_screener_page_restores_chart_period_dropdown_without_changing_ranking_inputs(self) -> None:
        source = Path('pages/screener.py').read_text(encoding='utf-8')
        self.assertIn("st.selectbox(\n        'Grafperiode'", source)
        self.assertIn('CHART_PERIOD_CALENDAR_MONTHS', source)
        self.assertIn('DEFAULT_CHART_PERIOD_LABEL', source)
        self.assertIn('chart_period_label=chart_period_label', source)
        ranking_call = source.split('build_incumbent_screener_ui_result(', 1)[1].split(')', 1)[0]
        self.assertNotIn('chart_period_label', ranking_call)

    def test_screener_page_exposes_chart_math_diagnostics_only_in_technical_expander(self) -> None:
        source = Path('pages/screener.py').read_text(encoding='utf-8')
        expander_body = source.split("with st.expander('Tekniske detaljer'", 1)[1]
        self.assertIn("'chart_period'", expander_body)
        self.assertIn("'chart_calendar_start_date'", expander_body)
        self.assertIn("'chart_baseline_date'", expander_body)
        self.assertIn("'chart_baseline_ticker_close'", expander_body)
        self.assertIn("'chart_baseline_benchmark_close'", expander_body)
        self.assertIn("'chart_last_indexed_ticker_value'", expander_body)
        self.assertIn("'chart_first_close'", expander_body)
        self.assertIn("'chart_last_close'", expander_body)
        self.assertIn("'chart_period_return_pct'", expander_body)
        self.assertIn("'chart_close_source'", expander_body)
        self.assertIn("'chart_raw_rows_loaded'", expander_body)
        self.assertIn("'chart_visible_rows'", expander_body)
        self.assertIn("'chart_first_normalized_date'", expander_body)
        self.assertIn("'chart_first_indexed_ticker_value'", expander_body)
        self.assertIn("'chart_first_indexed_benchmark_value'", expander_body)
        self.assertIn("'chart_first_rs_index_value'", expander_body)
        self.assertIn("'chart_sma50_non_null_count'", expander_body)
        self.assertIn("'chart_sma200_non_null_count'", expander_body)
        self.assertIn('Indeksert mot siste handelsdag før/ved periodestart.', source)

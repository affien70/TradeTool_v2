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

    def columns(self, count: int):
        return [self for _ in range(count)]

    def checkbox(self, *args, **kwargs) -> bool:
        return False

    def radio(self, label: str, options, index: int = 0, **kwargs):
        return options[index]

    def text_input(self, label: str, value: str = '', **kwargs) -> str:
        return value

    def text_area(self, label: str, value: str = '', **kwargs) -> str:
        return value

    def button(self, *args, **kwargs) -> bool:
        return False

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

        def _fail(*args, **kwargs):
            raise AssertionError('database access helper should not run during import')

        ui_module.build_minimal_screener_result = _fail
        ui_module.build_selected_ticker_chart_detail = _fail
        ui_module.build_selected_ticker_detail = _fail
        previous_streamlit = sys.modules.get('streamlit')
        previous_ui = sys.modules.get('tradetool.ui.screener')
        streamlit_module.session_state = {}
        sys.modules['streamlit'] = streamlit_module
        sys.modules['tradetool.ui.screener'] = ui_module
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

    def test_screener_page_has_no_automatic_db_access_on_page_load(self) -> None:
        source = Path('pages/screener.py').read_text(encoding='utf-8')
        self.assertIn("if st.button('Kjør diagnostisk screener')", source)
        prefix = source.split("if st.button('Kjør diagnostisk screener')", 1)[0]
        self.assertNotIn('build_minimal_screener_result(', prefix)
        self.assertIn("st.session_state.get('screener_result')", source)

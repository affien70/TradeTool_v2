from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class _StreamlitStub(types.ModuleType):
    def __init__(self) -> None:
        super().__init__('streamlit')
        self.session_state: dict[str, object] = {}
        self.pressed: set[str] = set()
        self.checked: set[str] = set()
        self.universe = 'NORWAY_V2'
        self.metrics: list[tuple[str, object]] = []
        self.captions: list[str] = []
        self.headers: list[str] = []
        self.buttons: dict[str, bool] = {}
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.json_values: list[dict[str, object]] = []
        self.reruns = 0
        self.text_inputs = 0

    def title(self, value: str) -> None:
        self.headers.append(value)

    def header(self, value: str) -> None:
        self.headers.append(value)

    def subheader(self, value: str) -> None:
        self.headers.append(value)

    def caption(self, value: str) -> None:
        self.captions.append(value)

    def write(self, value: str) -> None:
        self.captions.append(value)

    def info(self, value: str) -> None:
        self.captions.append(value)

    def warning(self, value: str) -> None:
        self.warnings.append(value)

    def error(self, value: str) -> None:
        self.errors.append(value)

    def json(self, value: dict[str, object]) -> None:
        self.json_values.append(value)

    def columns(self, count: int):
        return [self] * count

    def metric(self, label: str, value: object) -> None:
        self.metrics.append((label, value))

    def expander(self, *args, **kwargs):
        return self

    def spinner(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def selectbox(self, label: str, options, **kwargs) -> str:
        assert label == 'Univers' and tuple(options) == ('NORWAY_V2', 'SP500')
        return self.universe

    def checkbox(self, label: str, *, key: str, disabled: bool = False) -> bool:
        return key in self.checked and not disabled

    def button(self, label: str, *, key: str, disabled: bool = False) -> bool:
        self.buttons[key] = disabled
        return key in self.pressed and not disabled

    def text_input(self, *args, **kwargs) -> None:
        self.text_inputs += 1
        raise AssertionError('Settings must not expose a database path input')

    def rerun(self) -> None:
        self.reruns += 1


def _status(path: Path = Path('/tmp/tradetool_v2_settings_test.sqlite')):
    return types.SimpleNamespace(
        configured_path=path,
        configured_path_text=str(path),
        env_var_name='TRADETOOL_V2_DB_PATH',
        env_override_active=True,
        exists=True,
        readable=True,
        price_history_v2_table_exists=True,
        row_count=482358,
        latest_price_date='2026-09-17',
        error=None,
    )


def _result(universe: str, *, mode: str = 'dry-run', invalid: int = 2):
    return types.SimpleNamespace(
        universe_id=universe,
        target_db_path=_status().configured_path,
        mode=mode,
        outcome='preview' if mode == 'dry-run' else 'written',
        requested_tickers=('A', 'B', 'MISSING'),
        fetched_tickers=('A', 'B'),
        missing_tickers=('MISSING',),
        invalid_rows=invalid,
        invalid_rows_skipped=invalid if mode == 'write' else 0,
        would_insert=10,
        would_update=1,
        would_skip=2,
        inserted_rows=10 if mode == 'write' else 0,
        updated_rows=1 if mode == 'write' else 0,
        skipped_rows=2 if mode == 'write' else 0,
        tolerated_warnings={'raw_close_boundary': 1},
        source_warnings={},
        row_count=482368,
        latest_price_date='2026-09-17',
    )


class InnstillingerPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.st = _StreamlitStub()

    def _render(self, *, status=None, update_result=None, report_error=None, update_error=None):
        with patch.dict(sys.modules, {'streamlit': self.st}), \
                patch('tradetool.config.runtime_settings.inspect_app_database', return_value=status or _status()), \
                patch('tradetool.data.app_market_data_update.run_app_market_data_update', return_value=update_result or _result(self.st.universe)) as update, \
                patch('tradetool.data.app_market_data_update.write_app_market_data_update_report') as report:
            update.side_effect = update_error
            report.side_effect = report_error
            path = Path('pages/innstillinger.py').resolve()
            spec = importlib.util.spec_from_file_location('pages.innstillinger_test_import', path)
            module = importlib.util.module_from_spec(spec)
            assert spec is not None and spec.loader is not None
            spec.loader.exec_module(module)
        return update, report

    def test_status_and_controls_render_without_fetching_or_writing(self) -> None:
        update, report = self._render()
        update.assert_not_called()
        report.assert_not_called()
        self.assertIn('Oppdater lokal markedsdatabase', self.st.headers)
        self.assertIn(('Rader', 482358), self.st.metrics)
        self.assertIn('Siste prisdato: 2026-09-17', self.st.captions)
        self.assertIn('Benchmark: OSEBX.OL', self.st.captions)
        self.assertTrue(self.st.buttons['app_update_write'])
        self.assertFalse(self.st.buttons['app_update_dry_run'])
        self.assertEqual(self.st.text_inputs, 0)
        self.assertEqual(self.st.json_values[0]['configured_db_path'], str(_status().configured_path))

    def test_dry_run_uses_selected_universe_without_write_flags(self) -> None:
        self.st.universe = 'SP500'
        self.st.pressed.add('app_update_dry_run')
        update, report = self._render(update_result=_result('SP500'))
        update.assert_called_once_with(universe_id='SP500')
        report.assert_called_once()
        self.assertEqual(self.st.session_state['app_update_dry_result'].mode, 'dry-run')
        self.assertIn('Benchmark: ^GSPC', self.st.captions)
        self.assertIn(('Nye rader', 10), self.st.metrics)
        self.assertIn(('Manglende tickere', 1), self.st.metrics)
        self.assertIn(('Ugyldige rader', 2), self.st.metrics)
        self.assertTrue(any('Status: dry-run (preview)' in text for text in self.st.captions))
        self.assertTrue(any('Tolererte datavarsler' in text for text in self.st.captions))
        self.assertTrue(any('MISSING' in text for text in self.st.warnings))

    def test_write_requires_matching_dry_run_confirmation_and_separate_invalid_skip(self) -> None:
        self.st.session_state['app_update_dry_result'] = _result('NORWAY_V2')
        self.st.pressed.add('app_update_write')
        update, _ = self._render()
        update.assert_not_called()
        self.assertTrue(self.st.buttons['app_update_write'])

        self.st.checked.add('app_update_confirm')
        update, _ = self._render()
        update.assert_not_called()
        self.assertTrue(self.st.buttons['app_update_write'])

        self.st.checked.add('app_update_skip_invalid')
        update, report = self._render(update_result=_result('NORWAY_V2', mode='write'))
        update.assert_called_once_with(
            universe_id='NORWAY_V2', allow_app_db_write=True, allow_partial_invalid_skip=True,
        )
        report.assert_called_once()
        self.assertFalse(self.st.buttons['app_update_write'])
        self.assertEqual(self.st.reruns, 1)
        self.assertNotIn('app_update_dry_result', self.st.session_state)
        self.assertIn(('Oppdaterte rader', 1), self.st.metrics)
        self.assertTrue(any('Ugyldige rader hoppet over: 2' in text for text in self.st.captions))

    def test_other_universe_dry_run_cannot_authorize_write(self) -> None:
        self.st.universe = 'SP500'
        self.st.session_state['app_update_dry_result'] = _result('NORWAY_V2')
        self.st.checked.update({'app_update_confirm', 'app_update_skip_invalid'})
        self.st.pressed.add('app_update_write')
        update, _ = self._render()
        update.assert_not_called()
        self.assertTrue(self.st.buttons['app_update_write'])

    def test_failed_new_preview_clears_prior_write_authorization(self) -> None:
        self.st.session_state['app_update_dry_result'] = _result('NORWAY_V2')
        self.st.pressed.add('app_update_dry_run')
        update, _ = self._render(update_error=RuntimeError('provider unavailable'))
        update.assert_called_once_with(universe_id='NORWAY_V2')
        self.assertNotIn('app_update_dry_result', self.st.session_state)
        self.assertTrue(self.st.buttons['app_update_write'])
        self.assertTrue(any('provider unavailable' in error for error in self.st.errors))

    def test_report_failure_after_write_does_not_misreport_database_failure(self) -> None:
        self.st.session_state['app_update_dry_result'] = _result('NORWAY_V2')
        self.st.checked.update({'app_update_confirm', 'app_update_skip_invalid'})
        self.st.pressed.add('app_update_write')
        update, report = self._render(
            update_result=_result('NORWAY_V2', mode='write'),
            report_error=OSError('report unavailable'),
        )
        update.assert_called_once()
        report.assert_called_once()
        self.assertEqual(self.st.session_state['app_update_write_result'].outcome, 'written')
        self.assertEqual(self.st.reruns, 1)
        self.assertFalse(self.st.errors)
        self.assertIn('report unavailable', self.st.session_state['app_update_report_warning'])

    def test_unsafe_configured_target_disables_both_actions(self) -> None:
        unsafe = _status(Path('/Users/affien/DEV/TradeTool/portfolio.sqlite'))
        self.st.pressed.update({'app_update_dry_run', 'app_update_write'})
        self.st.checked.update({'app_update_confirm', 'app_update_skip_invalid'})
        update, _ = self._render(status=unsafe)
        update.assert_not_called()
        self.assertTrue(self.st.buttons['app_update_dry_run'])
        self.assertTrue(self.st.buttons['app_update_write'])
        self.assertTrue(any('deaktivert' in error for error in self.st.errors))

    def test_settings_page_does_not_contain_other_product_logic(self) -> None:
        source = Path('pages/innstillinger.py').read_text(encoding='utf-8')
        self.assertNotIn('st.text_input(', source)
        self.assertNotIn('screener_view', source)
        self.assertNotIn('holdings', source)
        self.assertNotIn('relative_strength_6m', source)


if __name__ == '__main__':
    unittest.main()

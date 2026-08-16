from __future__ import annotations

import unittest

from tradetool.config.settings import AppSettings, get_settings


class SettingsTests(unittest.TestCase):
    def test_settings_model_is_available(self) -> None:
        settings = get_settings()
        self.assertIsInstance(settings, AppSettings)

    def test_settings_contain_no_production_database_smtp_or_ml_artifact_selection(self) -> None:
        settings = get_settings()
        payload = {slot: getattr(settings, slot) for slot in settings.__slots__}
        forbidden_fragments = ('database', 'smtp', 'artifact', 'secret', 'threshold', 'engine')
        for key in payload:
            self.assertFalse(any(fragment in key.lower() for fragment in forbidden_fragments), key)
        self.assertEqual(settings.default_ui_language, 'no')
        self.assertEqual(settings.application_name, 'TradeTool v2')
        self.assertEqual(settings.contract_version, 'v2-phase2')

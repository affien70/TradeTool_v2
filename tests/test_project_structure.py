from __future__ import annotations

import unittest
from pathlib import Path

from tradetool.config.settings import get_settings


class ProjectStructureTests(unittest.TestCase):
    def test_expected_files_and_directories_exist(self) -> None:
        root = get_settings().project_root
        expected_paths = [
            'app.py',
            'pages/screener.py',
            'pages/beholdning.py',
            'pages/diagnostikk.py',
            'pages/innstillinger.py',
            'src/tradetool/contracts/models.py',
            'src/tradetool/contracts/enums.py',
            'src/tradetool/ui/page_config.py',
            'docs/specification/README.md',
            'evidence/v1_baseline/20260622T200335Z/summary.md',
            'reports/.gitkeep',
            'AGENTS.md',
            'README.md',
            'pyproject.toml',
            'requirements.txt',
            '.gitignore',
        ]
        for relative_path in expected_paths:
            self.assertTrue((root / relative_path).exists(), relative_path)

    def test_holdings_modules_stay_pure_without_runtime_integration(self) -> None:
        root = get_settings().project_root
        holdings_paths = (
            root / 'src/tradetool/holdings/core.py',
            root / 'src/tradetool/holdings/signals.py',
        )
        for holdings_path in holdings_paths:
            with self.subTest(holdings_path=holdings_path.name):
                text = holdings_path.read_text(encoding='utf-8').lower()
                self.assertNotIn('streamlit', text)
                self.assertNotIn('sqlite', text)

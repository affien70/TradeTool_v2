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

    def test_holdings_module_contains_no_implemented_signal_logic_yet(self) -> None:
        root = get_settings().project_root
        text = (root / 'src/tradetool/holdings/__init__.py').read_text(encoding='utf-8')
        self.assertIn('no signal logic is implemented yet', text)

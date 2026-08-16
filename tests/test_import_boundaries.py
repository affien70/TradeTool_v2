from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tradetool.config.settings import get_settings

FORBIDDEN_LEGACY = {
    'analytics',
    'db',
    'daily_holdings_report',
    'holdings_service',
    'holdings_view',
    'main',
    'ml',
    'ml_view',
    'screener_view',
    'tools_view',
}


def _imports_for(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split('.')[0])
    return imports


class ImportBoundaryTests(unittest.TestCase):
    def test_package_imports_do_not_import_streamlit_outside_ui(self) -> None:
        root = get_settings().project_root / 'src' / 'tradetool'
        for path in root.rglob('*.py'):
            if 'ui' in path.parts:
                continue
            self.assertNotIn('streamlit', _imports_for(path), str(path))

    def test_runtime_modules_do_not_import_legacy_tradetool_modules(self) -> None:
        root = get_settings().project_root / 'src' / 'tradetool'
        for package in ['runtime', 'universe', 'data', 'features', 'ranking', 'policy', 'explanation', 'holdings', 'artifacts', 'diagnostics']:
            for path in (root / package).rglob('*.py'):
                self.assertTrue(_imports_for(path).isdisjoint(FORBIDDEN_LEGACY), str(path))

    def test_page_modules_contain_no_imports_from_legacy_source_paths(self) -> None:
        pages_root = get_settings().project_root / 'pages'
        for path in pages_root.rglob('*.py'):
            self.assertTrue(_imports_for(path).isdisjoint(FORBIDDEN_LEGACY), str(path))

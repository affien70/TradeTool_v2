from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tradetool.config.settings import get_settings


class DiagnosticsPageTests(unittest.TestCase):
    def test_diagnostics_page_uses_v2_diagnostics_module_only(self) -> None:
        path = get_settings().project_root / 'pages' / 'diagnostikk.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        self.assertIn('tradetool.diagnostics.coverage', imports)
        self.assertNotIn('screener_view', imports)
        self.assertNotIn('ml', {name.split('.')[0] for name in imports})

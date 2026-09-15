from __future__ import annotations

from pathlib import Path
import unittest


class InnstillingerPageTests(unittest.TestCase):
    def test_settings_page_reports_configured_database_status(self) -> None:
        source = Path('pages/innstillinger.py').read_text(encoding='utf-8')
        self.assertIn('inspect_app_database()', source)
        self.assertIn("st.header('Database')", source)
        self.assertIn('configured_db_path', source)
        self.assertIn('environment_override_active', source)
        self.assertIn('price_history_v2_table_exists', source)
        self.assertIn('row_count', source)
        self.assertIn('latest_price_date', source)
        self.assertIn('DB-filer er lokale og skal ikke committes til Git.', source)


if __name__ == '__main__':
    unittest.main()

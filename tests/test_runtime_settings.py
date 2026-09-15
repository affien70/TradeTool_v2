from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tradetool.config.runtime_settings import (
    APP_DB_ENV_VAR,
    DEFAULT_APP_DB_PATH,
    get_app_database_path,
    inspect_app_database,
)


class RuntimeSettingsTests(unittest.TestCase):
    def test_default_app_database_path_is_local_and_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = get_app_database_path(env={}, cwd=Path(temp_dir))
            self.assertEqual(path, (Path(temp_dir) / DEFAULT_APP_DB_PATH).resolve())

    def test_environment_override_selects_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            override = str(Path(temp_dir) / 'override.sqlite')
            path = get_app_database_path(env={APP_DB_ENV_VAR: override}, cwd=Path('/tmp/unused'))
            self.assertEqual(path, Path(override).resolve())

    def test_missing_database_reports_clear_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status = inspect_app_database(env={}, cwd=Path(temp_dir))
            self.assertFalse(status.exists)
            self.assertFalse(status.readable)
            self.assertFalse(status.price_history_v2_table_exists)
            self.assertIsNone(status.row_count)
            self.assertIsNone(status.latest_price_date)

    def test_database_status_reports_price_history_v2_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'app.sqlite'
            with sqlite3.connect(db_path) as connection:
                connection.execute('CREATE TABLE price_history_v2 (ticker TEXT, price_date TEXT)')
                connection.executemany(
                    'INSERT INTO price_history_v2 VALUES (?, ?)',
                    [('AAA', '2026-09-08'), ('BBB', '2026-09-09')],
                )

            status = inspect_app_database(env={APP_DB_ENV_VAR: str(db_path)}, cwd=Path(temp_dir))
            self.assertTrue(status.env_override_active)
            self.assertTrue(status.exists)
            self.assertTrue(status.readable)
            self.assertTrue(status.price_history_v2_table_exists)
            self.assertEqual(status.row_count, 2)
            self.assertEqual(status.latest_price_date, '2026-09-09')

    def test_existing_database_without_price_history_v2_is_readable_but_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'app.sqlite'
            with sqlite3.connect(db_path) as connection:
                connection.execute('CREATE TABLE other_table (id INTEGER)')

            status = inspect_app_database(env={APP_DB_ENV_VAR: str(db_path)}, cwd=Path(temp_dir))
            self.assertTrue(status.exists)
            self.assertTrue(status.readable)
            self.assertFalse(status.price_history_v2_table_exists)
            self.assertIsNone(status.row_count)
            self.assertIsNone(status.latest_price_date)


if __name__ == '__main__':
    unittest.main()

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tradetool.data import ReadOnlySQLite


class ReadOnlySQLiteTests(unittest.TestCase):
    def test_missing_database_path_is_rejected_without_creating_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'missing.sqlite'
            with self.assertRaises(FileNotFoundError):
                ReadOnlySQLite(path)
            self.assertFalse(path.exists())

    def test_write_attempt_fails_in_read_only_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'fixture.sqlite'
            with sqlite3.connect(path) as connection:
                connection.execute('CREATE TABLE prices (ticker TEXT, date TEXT, close REAL)')
                connection.execute("INSERT INTO prices VALUES ('NHY.OL', '2026-08-14', 10.0)")
            database = ReadOnlySQLite(path)
            with database.connect() as connection:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("INSERT INTO prices VALUES ('AKRBP.OL', '2026-08-14', 20.0)")

    def test_non_select_helper_queries_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'fixture.sqlite'
            with sqlite3.connect(path) as connection:
                connection.execute('CREATE TABLE prices (ticker TEXT, date TEXT, close REAL)')
            database = ReadOnlySQLite(path)
            with self.assertRaises(ValueError):
                database.fetch_all('DELETE FROM prices')

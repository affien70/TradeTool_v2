from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tradetool.holdings import (
    DEFAULT_NORWAY_BENCHMARK_ID,
    HoldingSettings,
    dry_run_v1_holdings_settings_import,
    import_v1_holdings_settings,
    initialize_holdings_schema,
    load_holding_settings,
)


class HoldingsV1SettingsImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.v1_path = Path(self.temp_dir.name) / 'v1_settings.sqlite'
        self.v2_path = Path(self.temp_dir.name) / 'v2_holdings.sqlite'
        with sqlite3.connect(self.v1_path) as connection:
            connection.execute(
                '''
                CREATE TABLE app_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                '''
            )
        self.v2_connection = sqlite3.connect(self.v2_path)
        self.v2_connection.row_factory = sqlite3.Row
        initialize_holdings_schema(self.v2_connection)

    def tearDown(self) -> None:
        self.v2_connection.close()
        self.temp_dir.cleanup()

    def test_dry_run_maps_only_recognized_holdings_settings_without_writing(self) -> None:
        self._insert_v1_settings(
            period_label='5 år',
            rs_months='12',
            sell_rs_weak='ON',
            sell_below_cost_basis='no',
            sell_drop_from_peak='1',
            holdings_sell_sma_days='50',
            holdings_atr_multiplier='3.25',
            sell_rs_threshold='1.1',
            unrelated_secret='do not import',
        )
        v1_signature_before = self._signature(self.v1_path)
        result = dry_run_v1_holdings_settings_import(self.v1_path, target_connection=self.v2_connection)
        self.assertEqual(result.invalid_recognized_setting_keys, ())
        self.assertEqual(result.recognized_setting_keys_found, (
            'period_label', 'rs_months', 'sell_rs_weak', 'sell_below_cost_basis',
            'sell_drop_from_peak', 'holdings_sell_sma_days', 'holdings_atr_multiplier',
            'sell_rs_threshold',
        ))
        self.assertEqual(result.settings, HoldingSettings(
            period_label='5 år', rs_months=12, sell_rs_weak=True,
            sell_below_cost_basis=False, sell_drop_from_peak=True,
            sell_fast_sma_days=50, atr_multiplier=3.25, rs_threshold=1.1,
        ))
        self.assertEqual(result.written_rows, 0)
        self.assertEqual(load_holding_settings(self.v2_connection), HoldingSettings())
        self.assertEqual(self._signature(self.v1_path), v1_signature_before)
        self.assertNotIn('do not import', repr(result))

    def test_missing_v1_values_use_typed_defaults_and_never_migrate_v1_benchmark(self) -> None:
        self._insert_v1_settings(period_label='2 år', norway_benchmark_id='^OSEAX')
        result = dry_run_v1_holdings_settings_import(self.v1_path)
        self.assertEqual(result.settings, HoldingSettings(period_label='2 år'))
        self.assertIn('holdings_atr_multiplier', result.recognized_setting_keys_missing)
        self.assertIn('sell_rs_threshold', result.recognized_setting_keys_missing)
        self.assertEqual(result.settings.norway_benchmark_id, DEFAULT_NORWAY_BENCHMARK_ID)

    def test_invalid_recognized_values_are_reported_and_block_writes(self) -> None:
        self._insert_v1_settings(
            sell_rs_weak='sometimes',
            rs_months='9',
            holdings_atr_multiplier='NaN',
        )
        dry_run = dry_run_v1_holdings_settings_import(self.v1_path, target_connection=self.v2_connection)
        self.assertEqual(dry_run.invalid_recognized_setting_keys, (
            'rs_months', 'sell_rs_weak', 'holdings_atr_multiplier',
        ))
        self.assertIsNone(dry_run.settings)
        with self.assertRaisesRegex(ValueError, 'invalid recognized keys'):
            import_v1_holdings_settings(self.v1_path, target_connection=self.v2_connection)
        stored_rows = self.v2_connection.execute('SELECT COUNT(*) FROM holdings_settings_v2').fetchone()[0]
        self.assertEqual(stored_rows, 0)

    def test_write_requires_initialized_schema(self) -> None:
        self._insert_v1_settings(period_label='5 år')
        with sqlite3.connect(Path(self.temp_dir.name) / 'uninitialized.sqlite') as connection:
            with self.assertRaisesRegex(ValueError, 'initialized explicitly'):
                import_v1_holdings_settings(self.v1_path, target_connection=connection)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0], 0)

    def test_write_is_idempotent_and_allows_explicit_v2_benchmark_override(self) -> None:
        self._insert_v1_settings(period_label='5 år', holdings_sell_sma_days='0')
        first = import_v1_holdings_settings(
            self.v1_path,
            target_connection=self.v2_connection,
            norway_benchmark_id='CUSTOM.OL',
        )
        second = import_v1_holdings_settings(
            self.v1_path,
            target_connection=self.v2_connection,
            norway_benchmark_id='CUSTOM.OL',
        )
        expected = HoldingSettings(period_label='5 år', sell_fast_sma_days=0, norway_benchmark_id='CUSTOM.OL')
        self.assertEqual(first.settings, expected)
        self.assertEqual(second.settings, expected)
        self.assertEqual(first.written_rows, 1)
        self.assertEqual(second.written_rows, 1)
        self.assertEqual(load_holding_settings(self.v2_connection), expected)
        self.assertEqual(self.v2_connection.execute('SELECT COUNT(*) FROM holdings_settings_v2').fetchone()[0], 1)

    def _insert_v1_settings(self, **settings: str) -> None:
        with sqlite3.connect(self.v1_path) as connection:
            connection.executemany(
                'INSERT INTO app_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)',
                ((key, value, '2025-01-01T00:00:00Z') for key, value in settings.items()),
            )

    @staticmethod
    def _signature(path: Path) -> tuple[int, int]:
        return path.stat().st_mtime_ns, path.stat().st_size


if __name__ == '__main__':
    unittest.main()
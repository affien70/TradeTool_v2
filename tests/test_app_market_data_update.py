from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from unittest.mock import patch

from tradetool.config.runtime_settings import APP_DB_ENV_VAR
from tradetool.data.app_market_data_update import run_app_market_data_update, validate_app_update_target
from tradetool.data.app_market_data_update_cli import main as cli_main
from tradetool.data.market_data_schema import LEGACY_PRODUCTION_DB_PATH, REPO_LOCAL_DIR
from tradetool.data.market_data_source import MarketDataSourceFetchResult, SourceMarketDataRow, TickerFetchStatus


def _row(ticker: str, **changes) -> SourceMarketDataRow:
    values = {
        'ticker': ticker, 'price_date': '2026-09-15', 'raw_open': 100.0,
        'raw_high': 105.0, 'raw_low': 99.0, 'raw_close': 104.0,
        'adjusted_close': 102.0, 'volume': 1000.0, 'data_source': 'yahoo',
    }
    values.update(changes)
    return SourceMarketDataRow(**values)


@dataclass
class _Source:
    rows_by_ticker: dict[str, SourceMarketDataRow]
    calls: list[tuple[tuple[str, ...], date, date]] = field(default_factory=list)
    source_name: str = 'yahoo'

    def fetch_daily_rows(self, *, tickers, start_date, end_date) -> MarketDataSourceFetchResult:
        requested = tuple(tickers)
        self.calls.append((requested, start_date, end_date))
        rows = tuple(self.rows_by_ticker[ticker] for ticker in requested if ticker in self.rows_by_ticker)
        statuses = tuple(
            TickerFetchStatus(ticker, ticker in self.rows_by_ticker, int(ticker in self.rows_by_ticker),
                              'fetched' if ticker in self.rows_by_ticker else 'missing')
            for ticker in requested
        )
        return MarketDataSourceFetchResult('yahoo', requested, rows, statuses)


class AppMarketDataUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'app.sqlite'
        self.universe_db = self.root / 'universe.sqlite'
        with sqlite3.connect(self.universe_db) as connection:
            connection.execute('CREATE TABLE universe_cache (universe_key TEXT PRIMARY KEY, tickers_json TEXT)')
            connection.executemany(
                'INSERT INTO universe_cache VALUES (?, ?)',
                [('NORWAY_V2', json.dumps(['CAMBI.OL', 'MISSING.OL', 'OSEBX.OL'])),
                 ('SP500', json.dumps(['AMD', '^GSPC']))],
            )

    def _run(self, universe: str, source: _Source, *, write: bool = False, partial: bool = False):
        return run_app_market_data_update(
            universe_id=universe,
            allow_app_db_write=write,
            allow_partial_invalid_skip=partial,
            end_date=date(2026, 9, 17),
            universe_db_path=self.universe_db,
            env={APP_DB_ENV_VAR: str(self.target)},
            source_override=source,
        )

    def test_dry_run_previews_without_creating_database_or_changing_universe_source(self) -> None:
        source = _Source({'CAMBI.OL': _row('CAMBI.OL'), 'OSEBX.OL': _row('OSEBX.OL')})
        before = self.universe_db.read_bytes()
        result = self._run('NORWAY_V2', source)
        self.assertEqual(result.mode, 'dry-run')
        self.assertEqual(result.inserted_rows, 0)
        self.assertEqual(result.would_insert, 2)
        self.assertEqual(result.missing_tickers, ('MISSING.OL',))
        self.assertEqual(result.universe_source, 'universe_cache:NORWAY_V2')
        self.assertEqual(result.benchmark_ticker, 'OSEBX.OL')
        self.assertFalse(self.target.exists())
        self.assertEqual(self.universe_db.read_bytes(), before)

    def test_explicit_write_initializes_only_v2_table_and_is_idempotent(self) -> None:
        source = _Source({'CAMBI.OL': _row('CAMBI.OL'), 'OSEBX.OL': _row('OSEBX.OL')})
        first = self._run('NORWAY_V2', source, write=True)
        second = self._run('NORWAY_V2', source, write=True)
        with sqlite3.connect(self.target) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            count = connection.execute('SELECT COUNT(*) FROM price_history_v2').fetchone()[0]
        self.assertEqual(tables, {'price_history_v2'})
        self.assertEqual((first.inserted_rows, first.updated_rows, first.row_count), (2, 0, 2))
        self.assertEqual((second.inserted_rows, second.updated_rows, second.skipped_rows, count), (0, 0, 2, 2))
        self.assertEqual(second.latest_price_date, '2026-09-15')
        self.assertEqual(second.start_dates['CAMBI.OL'], '2026-09-08')

    def test_changed_existing_row_is_updated(self) -> None:
        self._run('SP500', _Source({'AMD': _row('AMD'), '^GSPC': _row('^GSPC')}), write=True)
        changed = self._run('SP500', _Source({'AMD': _row('AMD', adjusted_close=103.0), '^GSPC': _row('^GSPC')}), write=True)
        self.assertEqual(changed.universe_source, 'universe_cache:SP500')
        self.assertEqual(changed.benchmark_ticker, '^GSPC')
        self.assertEqual((changed.inserted_rows, changed.updated_rows, changed.skipped_rows), (0, 1, 1))
        with sqlite3.connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT adjusted_close FROM price_history_v2 WHERE ticker = 'AMD'").fetchone()[0], 103.0)

    def test_dry_run_does_not_update_existing_database(self) -> None:
        self._run('SP500', _Source({'AMD': _row('AMD'), '^GSPC': _row('^GSPC')}), write=True)
        before = self.target.read_bytes()
        preview = self._run('SP500', _Source({'AMD': _row('AMD', adjusted_close=103.0), '^GSPC': _row('^GSPC')}))
        self.assertEqual(preview.would_update, 1)
        self.assertEqual(preview.updated_rows, 0)
        self.assertEqual(self.target.read_bytes(), before)

    def test_invalid_rows_block_default_write_and_explicit_partial_mode_skips_them(self) -> None:
        source = _Source({'CAMBI.OL': _row('CAMBI.OL', raw_high=90.0), 'OSEBX.OL': _row('OSEBX.OL')})
        blocked = self._run('NORWAY_V2', source, write=True)
        self.assertEqual(blocked.outcome, 'blocked_invalid_rows')
        self.assertEqual(blocked.invalid_rows, 1)
        self.assertFalse(self.target.exists())
        partial = self._run('NORWAY_V2', source, write=True, partial=True)
        self.assertEqual((partial.inserted_rows, partial.invalid_rows_skipped, partial.row_count), (1, 1, 1))
        self.assertIn('raw_high_lower_than_raw_low', partial.invalid_reasons)

    def test_tolerated_and_source_warnings_are_reported(self) -> None:
        source = _Source({
            'AMD': _row('AMD', raw_open=105.0, raw_low=104.05, warning_codes=('adjusted_close_fallback_to_raw_close',)),
            '^GSPC': _row('^GSPC'),
        })
        result = self._run('SP500', source)
        self.assertEqual(result.invalid_rows, 0)
        self.assertEqual(result.tolerated_warnings['raw_low_higher_than_raw_close_tolerated'], 1)
        self.assertEqual(result.source_warnings['adjusted_close_fallback_to_raw_close'], 1)

    def test_target_safety_accepts_local_and_refuses_legacy_or_external_path(self) -> None:
        self.assertEqual(validate_app_update_target(REPO_LOCAL_DIR / 'safe.sqlite', env_override_active=False), REPO_LOCAL_DIR / 'safe.sqlite')
        self.assertEqual(validate_app_update_target(self.target, env_override_active=True), self.target.resolve())
        for unsafe in (LEGACY_PRODUCTION_DB_PATH, LEGACY_PRODUCTION_DB_PATH.parent / 'copy.sqlite', Path('/Users/affien/outside.sqlite')):
            with self.subTest(path=unsafe), self.assertRaises(ValueError):
                validate_app_update_target(unsafe, env_override_active=True)
        with self.assertRaises(ValueError):
            validate_app_update_target(self.root / 'missing' / 'app.sqlite', env_override_active=True)

    def test_cli_defaults_to_dry_run_and_requires_flag_for_partial_write(self) -> None:
        source = _Source({'AMD': _row('AMD'), '^GSPC': _row('^GSPC')})
        with patch.dict(os.environ, {APP_DB_ENV_VAR: str(self.target)}), \
                patch('tradetool.data.app_market_data_update.build_market_data_source', return_value=source), \
                patch('tradetool.data.app_market_data_update.REPORT_ROOT', self.root / 'reports'):
            self.assertEqual(cli_main(['--universe', 'SP500', '--universe-db-path', str(self.universe_db), '--end-date', '2026-09-17']), 0)
            self.assertFalse(self.target.exists())
            reports = list((self.root / 'reports').glob('*/app_db_update_summary.json'))
            self.assertEqual(len(reports), 1)
            self.assertEqual(json.loads(reports[0].read_text())['mode'], 'dry-run')
            with self.assertRaises(SystemExit):
                cli_main(['--universe', 'SP500', '--allow-partial-invalid-skip'])


if __name__ == '__main__':
    unittest.main()

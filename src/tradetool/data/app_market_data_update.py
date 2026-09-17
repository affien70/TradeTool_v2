from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import json
import os
from pathlib import Path

from tradetool.config.runtime_settings import APP_DB_ENV_VAR, get_app_database_path, inspect_app_database
from tradetool.data.market_data_schema import (
    LEGACY_PRODUCTION_DB_PATH,
    REPO_LOCAL_DIR,
    SYSTEM_TMP_DIR,
    dry_run_market_data_rows,
    validate_market_data_db_path,
    write_market_data_rows_for_test,
)
from tradetool.data.market_data_source import MarketDataSource, build_market_data_source, normalize_source_rows_for_v2
from tradetool.data.sqlite_readonly import ReadOnlySQLite
from tradetool.universe.tickers import load_universe_tickers

UNIVERSE_BENCHMARKS = {'NORWAY_V2': 'OSEBX.OL', 'SP500': '^GSPC'}
REPORT_ROOT = Path(__file__).resolve().parents[3] / 'reports' / 'app_db_update'


@dataclass(frozen=True, slots=True)
class AppMarketDataUpdateResult:
    target_db_path: Path
    universe_id: str
    universe_source: str
    benchmark_ticker: str
    data_source: str
    mode: str
    outcome: str
    requested_tickers: tuple[str, ...]
    fetched_tickers: tuple[str, ...]
    missing_tickers: tuple[str, ...]
    inserted_rows: int
    updated_rows: int
    skipped_rows: int
    invalid_rows: int
    invalid_rows_skipped: int
    would_insert: int
    would_update: int
    would_skip: int
    invalid_reasons: Mapping[str, int]
    tolerated_warnings: Mapping[str, int]
    source_warnings: Mapping[str, int]
    row_count: int
    latest_price_date: str | None
    start_dates: Mapping[str, str]
    end_date: str

    def to_dict(self) -> dict[str, object]:
        return {
            'target_db_path': str(self.target_db_path),
            'universe_id': self.universe_id,
            'universe_source': self.universe_source,
            'benchmark_ticker': self.benchmark_ticker,
            'data_source': self.data_source,
            'mode': self.mode,
            'outcome': self.outcome,
            'requested_tickers': list(self.requested_tickers),
            'requested_ticker_count': len(self.requested_tickers),
            'fetched_tickers': list(self.fetched_tickers),
            'fetched_ticker_count': len(self.fetched_tickers),
            'missing_tickers': list(self.missing_tickers),
            'missing_ticker_count': len(self.missing_tickers),
            'inserted_rows': self.inserted_rows,
            'updated_rows': self.updated_rows,
            'skipped_rows': self.skipped_rows,
            'invalid_rows': self.invalid_rows,
            'invalid_rows_skipped': self.invalid_rows_skipped,
            'would_insert': self.would_insert,
            'would_update': self.would_update,
            'would_skip': self.would_skip,
            'invalid_reasons': dict(self.invalid_reasons),
            'tolerated_warnings': dict(self.tolerated_warnings),
            'source_warnings': dict(self.source_warnings),
            'row_count': self.row_count,
            'latest_price_date': self.latest_price_date,
            'start_dates': dict(self.start_dates),
            'end_date': self.end_date,
        }


def validate_app_update_target(path: str | Path, *, env_override_active: bool) -> Path:
    resolved = Path(path).expanduser().resolve()
    if resolved == LEGACY_PRODUCTION_DB_PATH or resolved.is_relative_to(LEGACY_PRODUCTION_DB_PATH.parent):
        raise ValueError(f'Legacy repository is refused as an app DB write target: {resolved}')
    if not (resolved.is_relative_to(REPO_LOCAL_DIR) or (env_override_active and resolved.is_relative_to(SYSTEM_TMP_DIR))):
        raise ValueError('App DB writes are limited to v2 .local/ or an explicit /tmp environment override.')
    if resolved.parent != REPO_LOCAL_DIR and not resolved.parent.is_dir():
        raise ValueError(f'App DB parent directory does not exist: {resolved.parent}')
    if resolved.exists() and not resolved.is_file():
        raise ValueError(f'App DB target is not a file: {resolved}')
    return validate_market_data_db_path(resolved)


def run_app_market_data_update(
    *,
    universe_id: str,
    allow_app_db_write: bool = False,
    allow_partial_invalid_skip: bool = False,
    start_date: date | None = None,
    end_date: date | None = None,
    universe_db_path: str | Path = LEGACY_PRODUCTION_DB_PATH,
    env: Mapping[str, str] | None = None,
    source_override: MarketDataSource | None = None,
) -> AppMarketDataUpdateResult:
    if universe_id not in UNIVERSE_BENCHMARKS:
        raise ValueError(f'Unsupported universe: {universe_id}')
    active_env = os.environ if env is None else env
    target = validate_app_update_target(
        get_app_database_path(env=active_env, cwd=Path(__file__).resolve().parents[3]),
        env_override_active=bool(active_env.get(APP_DB_ENV_VAR, '').strip()),
    )
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError('Start date must not be after end date.')
    effective_end = end_date or date.today()
    if start_date is not None and start_date > effective_end:
        raise ValueError('Start date must not be after end date.')

    universe = load_universe_tickers(universe_id=universe_id, database=ReadOnlySQLite(universe_db_path))
    benchmark = UNIVERSE_BENCHMARKS[universe_id]
    stocks = tuple(sorted(ticker for ticker in universe.tickers if ticker != benchmark))
    if not stocks:
        raise ValueError(f'Universe {universe_id} has no stock tickers.')
    requested = tuple(sorted((*stocks, benchmark)))
    before = inspect_app_database(env={APP_DB_ENV_VAR: str(target)})
    if before.exists and not before.readable:
        raise ValueError(f'Existing app DB is not readable: {before.error}')
    latest_by_ticker = _latest_dates_by_ticker(target, requested) if before.price_history_v2_table_exists else {}
    bootstrap_start = _two_year_start(effective_end)
    start_dates = {
        ticker: start_date or max(bootstrap_start, min(latest_by_ticker.get(ticker, bootstrap_start), effective_end) - timedelta(days=7))
        for ticker in requested
    }
    groups: dict[date, list[str]] = defaultdict(list)
    for ticker, ticker_start in start_dates.items():
        groups[ticker_start].append(ticker)

    source = source_override or build_market_data_source('yahoo')
    fetched_rows = []
    fetched_tickers: set[str] = set()
    source_warnings: Counter[str] = Counter()
    for ticker_start, group in sorted(groups.items()):
        fetched = source.fetch_daily_rows(tickers=group, start_date=ticker_start, end_date=effective_end)
        if fetched.provider_error:
            raise RuntimeError(f'Market-data source failed: {fetched.provider_error}')
        fetched_rows.extend(fetched.rows)
        fetched_tickers.update(status.ticker for status in fetched.ticker_statuses if status.fetched)
        source_warnings.update(code for row in fetched.rows for code in row.warning_codes)
    missing_tickers = tuple(sorted(set(requested) - fetched_tickers))
    timestamp = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    rows = normalize_source_rows_for_v2(rows=fetched_rows, created_at_utc=timestamp, updated_at_utc=timestamp)
    preflight = dry_run_market_data_rows(db_path=target, rows=rows)
    mode = 'write' if allow_app_db_write else 'dry-run'
    outcome = 'preview' if not allow_app_db_write else 'written'
    inserted = updated = skipped = invalid_skipped = 0
    if allow_app_db_write:
        if preflight.invalid_row_count and not allow_partial_invalid_skip:
            outcome = 'blocked_invalid_rows'
        elif rows:
            valid_rows = tuple(row for row, validation in zip(rows, preflight.rows, strict=True) if validation.valid)
            write_result = write_market_data_rows_for_test(
                db_path=target, rows=valid_rows, allow_test_db_write=True,
            )
            inserted = write_result.inserted_count
            updated = write_result.updated_count
            skipped = write_result.skipped_count
            invalid_skipped = preflight.invalid_row_count
        else:
            outcome = 'no_rows_fetched'
    after = inspect_app_database(env={APP_DB_ENV_VAR: str(target)})
    return AppMarketDataUpdateResult(
        target_db_path=target,
        universe_id=universe_id,
        universe_source=universe.source,
        benchmark_ticker=benchmark,
        data_source='yahoo',
        mode=mode,
        outcome=outcome,
        requested_tickers=requested,
        fetched_tickers=tuple(sorted(fetched_tickers)),
        missing_tickers=missing_tickers,
        inserted_rows=inserted,
        updated_rows=updated,
        skipped_rows=skipped,
        invalid_rows=preflight.invalid_row_count,
        invalid_rows_skipped=invalid_skipped,
        would_insert=preflight.would_insert,
        would_update=preflight.would_update,
        would_skip=preflight.would_skip,
        invalid_reasons=preflight.invalid_reasons,
        tolerated_warnings=preflight.tolerated_warnings,
        source_warnings=dict(sorted(source_warnings.items())),
        row_count=after.row_count or 0,
        latest_price_date=after.latest_price_date,
        start_dates={ticker: ticker_start.isoformat() for ticker, ticker_start in start_dates.items()},
        end_date=effective_end.isoformat(),
    )


def write_app_market_data_update_report(result: AppMarketDataUpdateResult) -> Path:
    out_dir = REPORT_ROOT / f'{datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")}_{result.universe_id}_{result.mode}'
    out_dir.mkdir(parents=True, exist_ok=False)
    summary = result.to_dict()
    (out_dir / 'app_db_update_summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8')
    lines = ['# App market-data update', '']
    for key, value in summary.items():
        if key == 'start_dates':
            continue
        lines.append(f'- {key}: {value}')
    lines.append('')
    (out_dir / 'app_db_update_summary.md').write_text('\n'.join(lines), encoding='utf-8')
    return out_dir


def _latest_dates_by_ticker(path: Path, tickers: tuple[str, ...]) -> dict[str, date]:
    placeholders = ', '.join('?' for _ in tickers)
    rows = ReadOnlySQLite(path).fetch_all(
        f'SELECT ticker, MAX(price_date) AS latest FROM price_history_v2 WHERE ticker IN ({placeholders}) AND data_source = ? GROUP BY ticker',
        (*tickers, 'yahoo'),
    )
    return {str(row['ticker']): date.fromisoformat(str(row['latest'])) for row in rows if row['latest']}


def _two_year_start(end_date: date) -> date:
    try:
        return end_date.replace(year=end_date.year - 2)
    except ValueError:
        return end_date.replace(year=end_date.year - 2, day=28)

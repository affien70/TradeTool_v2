from __future__ import annotations

import argparse
from datetime import date

from tradetool.data.app_market_data_update import (
    UNIVERSE_BENCHMARKS,
    run_app_market_data_update,
    write_app_market_data_update_report,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Preview or update the configured local v2 app market-data database.')
    parser.add_argument('--universe', required=True, choices=tuple(UNIVERSE_BENCHMARKS))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Preview only (also the default).')
    mode.add_argument('--allow-app-db-write', action='store_true', help='Explicitly allow writes to the safe local app DB.')
    parser.add_argument('--allow-partial-invalid-skip', action='store_true', help='With a write, skip invalid rows and write valid rows.')
    parser.add_argument('--start-date', type=date.fromisoformat, help='Override incremental/bootstrap fetch start (YYYY-MM-DD).')
    parser.add_argument('--end-date', type=date.fromisoformat, help='Inclusive fetch end date (YYYY-MM-DD). Defaults to today.')
    parser.add_argument('--universe-db-path', help='Read-only universe_cache source; defaults to the legacy reference DB.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    if args.allow_partial_invalid_skip and not args.allow_app_db_write:
        parser.error('--allow-partial-invalid-skip requires --allow-app-db-write.')
    kwargs = {}
    if args.universe_db_path:
        kwargs['universe_db_path'] = args.universe_db_path
    result = run_app_market_data_update(
        universe_id=args.universe,
        allow_app_db_write=args.allow_app_db_write,
        allow_partial_invalid_skip=args.allow_partial_invalid_skip,
        start_date=args.start_date,
        end_date=args.end_date,
        **kwargs,
    )
    out_dir = write_app_market_data_update_report(result)
    summary = result.to_dict()
    for key in (
        'mode', 'outcome', 'target_db_path', 'universe_id', 'universe_source', 'benchmark_ticker',
        'data_source', 'requested_ticker_count', 'fetched_ticker_count', 'missing_tickers',
        'inserted_rows', 'updated_rows', 'skipped_rows', 'invalid_rows', 'invalid_rows_skipped',
        'would_insert', 'would_update', 'would_skip', 'tolerated_warnings', 'source_warnings',
        'row_count', 'latest_price_date',
    ):
        print(f'{key}: {summary[key]}')
    print(f'report_dir: {out_dir}')
    return 0 if result.outcome in {'preview', 'written'} else 1


if __name__ == '__main__':
    raise SystemExit(main())

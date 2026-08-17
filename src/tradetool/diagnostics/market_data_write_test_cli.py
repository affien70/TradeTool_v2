from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from tradetool.diagnostics.market_data_write_test import (
    build_market_data_write_test_result,
    write_market_data_write_test_outputs,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Fetch, validate, and write market-data rows into a temporary test DB only.')
    parser.add_argument('--db-path', required=True, help='Path to an explicit temporary or test SQLite DB.')
    parser.add_argument('--tickers', nargs='+', required=True, help='Explicit ticker list for fetch validation.')
    parser.add_argument('--start-date', required=True, help='Inclusive start date in YYYY-MM-DD format.')
    parser.add_argument('--end-date', help='Inclusive end date in YYYY-MM-DD format. Defaults to today.')
    parser.add_argument('--source', required=True, help='Market-data source name, for example yahoo.')
    parser.add_argument('--allow-test-db-write', action='store_true', help='Required acknowledgement for writing to a temporary test DB.')
    parser.add_argument('--out-dir', required=True, help='Output directory for the generated test-write report files.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    if not args.allow_test_db_write:
        parser.error('--allow-test-db-write is required for any temporary market-data write.')
    start_date = date.fromisoformat(args.start_date)
    end_date = date.today() if args.end_date is None else date.fromisoformat(args.end_date)
    result = build_market_data_write_test_result(
        db_path=Path(args.db_path),
        tickers=list(args.tickers),
        start_date=start_date,
        end_date=end_date,
        source_name=args.source,
        allow_test_db_write=True,
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_market_data_write_test_outputs(result=result, out_dir=out_dir)
    print(f'VALID market data write test diagnostics written to {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

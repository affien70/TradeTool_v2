from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from tradetool.diagnostics.market_data_fetch_dry_run import (
    build_market_data_fetch_dry_run,
    write_market_data_fetch_dry_run_outputs,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Generate dry-run market-data fetch diagnostics without writing rows to a DB.')
    parser.add_argument('--db-path', required=True, help='Path to an explicit temporary or test SQLite DB.')
    parser.add_argument('--tickers', nargs='+', required=True, help='Explicit ticker list for dry-run fetch validation.')
    parser.add_argument('--start-date', required=True, help='Inclusive start date in YYYY-MM-DD format.')
    parser.add_argument('--end-date', help='Inclusive end date in YYYY-MM-DD format. Defaults to today.')
    parser.add_argument('--source', required=True, help='Market-data source name, for example yahoo.')
    parser.add_argument('--out-dir', required=True, help='Output directory for the generated dry-run report files.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    start_date = date.fromisoformat(args.start_date)
    end_date = date.today() if args.end_date is None else date.fromisoformat(args.end_date)
    result = build_market_data_fetch_dry_run(
        db_path=Path(args.db_path),
        tickers=list(args.tickers),
        start_date=start_date,
        end_date=end_date,
        source_name=args.source,
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_market_data_fetch_dry_run_outputs(result=result, out_dir=out_dir)
    print(f'VALID market data fetch dry run diagnostics written to {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

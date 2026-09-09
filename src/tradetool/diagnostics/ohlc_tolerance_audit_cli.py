from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from tradetool.diagnostics.ohlc_tolerance_audit import (
    build_ohlc_tolerance_audit,
    write_ohlc_tolerance_audit_outputs,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Audit strict OHLC validation failures against tolerance scenarios without any DB access.')
    parser.add_argument('--tickers', nargs='+', required=True, help='Explicit ticker list for fetch-only OHLC tolerance audit.')
    parser.add_argument('--start-date', required=True, help='Inclusive start date in YYYY-MM-DD format.')
    parser.add_argument('--end-date', help='Inclusive end date in YYYY-MM-DD format. Defaults to today.')
    parser.add_argument('--source', required=True, help='Market-data source name, for example yahoo.')
    parser.add_argument('--out-dir', required=True, help='Output directory for generated OHLC tolerance audit files.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    result = build_ohlc_tolerance_audit(
        tickers=list(args.tickers),
        start_date=date.fromisoformat(args.start_date),
        end_date=date.today() if args.end_date is None else date.fromisoformat(args.end_date),
        source_name=args.source,
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_ohlc_tolerance_audit_outputs(result=result, out_dir=out_dir)
    print(f'VALID OHLC tolerance audit written to {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

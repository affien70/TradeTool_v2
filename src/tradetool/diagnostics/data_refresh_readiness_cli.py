from __future__ import annotations

import argparse
from pathlib import Path

from tradetool.diagnostics.data_refresh_readiness import (
    build_data_refresh_readiness_audit,
    write_data_refresh_readiness_outputs,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Generate read-only data-refresh readiness diagnostics from a SQLite copy.')
    parser.add_argument('--db-path', required=True, help='Path to a local SQLite copy.')
    parser.add_argument('--universe-id', required=True, help='Universe identifier for the readiness audit.')
    parser.add_argument('--benchmark-ticker', help='Optional benchmark ticker.')
    parser.add_argument('--out-dir', required=True, help='Output directory for the generated readiness files.')
    parser.add_argument('--price-table', help='Optional explicit price table.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    result = build_data_refresh_readiness_audit(
        db_path=Path(args.db_path),
        universe_id=args.universe_id,
        benchmark_ticker=args.benchmark_ticker,
        price_table=args.price_table,
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_data_refresh_readiness_outputs(result=result, out_dir=out_dir)
    print(f'VALID data refresh readiness diagnostics written to {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

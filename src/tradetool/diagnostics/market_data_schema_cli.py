from __future__ import annotations

import argparse
from pathlib import Path

from tradetool.diagnostics.market_data_schema import build_market_data_schema_report, write_market_data_schema_report


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Generate read-only v2 market-data schema diagnostics from a test SQLite DB.')
    parser.add_argument('--db-path', required=True, help='Path to an explicit temporary or test SQLite DB.')
    parser.add_argument('--out-dir', required=True, help='Output directory for the generated schema report files.')
    parser.add_argument('--init-schema', action='store_true', help='Create the empty v2 market-data schema before inspection.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    report = build_market_data_schema_report(
        db_path=Path(args.db_path),
        init_schema=args.init_schema,
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_market_data_schema_report(report=report, out_dir=out_dir)
    print(f'VALID market data schema diagnostics written to {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

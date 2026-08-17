from __future__ import annotations

import argparse
from pathlib import Path

from tradetool.diagnostics.invalid_ohlc import build_invalid_ohlc_diagnostics, write_invalid_ohlc_outputs


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Generate read-only invalid-OHLC diagnostics from a SQLite copy.')
    parser.add_argument('--db-path', required=True, help='Path to a local SQLite copy.')
    parser.add_argument('--universe-id', required=True, help='Universe identifier for the invalid-OHLC report.')
    parser.add_argument('--out-dir', required=True, help='Output directory for the generated invalid-OHLC files.')
    parser.add_argument('--price-table', help='Optional explicit price table.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    result = build_invalid_ohlc_diagnostics(
        db_path=Path(args.db_path),
        universe_id=args.universe_id,
        price_table=args.price_table,
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_invalid_ohlc_outputs(result=result, out_dir=out_dir)
    print(f'VALID invalid OHLC diagnostics written to {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

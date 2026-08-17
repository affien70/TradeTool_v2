from __future__ import annotations

import argparse
from pathlib import Path

from tradetool.diagnostics.ohlc_interpretation import (
    build_ohlc_interpretation_audit,
    write_ohlc_interpretation_outputs,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Generate read-only OHLC interpretation diagnostics from a SQLite copy.')
    parser.add_argument('--db-path', required=True, help='Path to a local SQLite copy.')
    parser.add_argument('--universe-id', required=True, help='Universe identifier for the OHLC interpretation report.')
    parser.add_argument('--out-dir', required=True, help='Output directory for the generated OHLC interpretation files.')
    parser.add_argument('--price-table', help='Optional explicit price table.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    result = build_ohlc_interpretation_audit(
        db_path=Path(args.db_path),
        universe_id=args.universe_id,
        price_table=args.price_table,
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_ohlc_interpretation_outputs(result=result, out_dir=out_dir)
    print(f'VALID OHLC interpretation diagnostics written to {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

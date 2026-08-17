from __future__ import annotations

import argparse
from pathlib import Path

from tradetool.diagnostics.market_data_v2_readiness import (
    build_market_data_v2_readiness,
    write_market_data_v2_readiness_outputs,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Generate read-only price_history_v2 readiness diagnostics from a temporary test DB.')
    parser.add_argument('--db-path', required=True, help='Path to an explicit temporary or test SQLite DB.')
    parser.add_argument('--tickers', nargs='+', required=True, help='Explicit ticker list for v2 read-path validation.')
    parser.add_argument('--benchmark-ticker', help='Optional benchmark ticker to include in the readiness audit.')
    parser.add_argument('--data-source', default='yahoo', help='Explicit price_history_v2 data source filter.')
    parser.add_argument('--out-dir', required=True, help='Output directory for the generated readiness report files.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    result = build_market_data_v2_readiness(
        db_path=Path(args.db_path),
        tickers=list(args.tickers),
        benchmark_ticker=args.benchmark_ticker,
        data_source=args.data_source,
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_market_data_v2_readiness_outputs(result=result, out_dir=out_dir)
    print(f'VALID market data v2 readiness diagnostics written to {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

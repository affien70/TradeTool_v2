from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from tradetool.ranking.incumbent_screener import (
    build_incumbent_screener,
    write_incumbent_screener_outputs,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run the incumbent 6m relative-strength screener core for one current snapshot.')
    parser.add_argument('--db-path', required=True, help='Temporary price_history_v2 SQLite DB path to read.')
    parser.add_argument('--universe-id', required=True, help='Universe ID to load from read-only universe_cache.')
    parser.add_argument('--benchmark-ticker', required=True, help='Benchmark ticker for relative strength context.')
    parser.add_argument('--as-of-date', required=True, help='Snapshot as-of date in YYYY-MM-DD format.')
    parser.add_argument('--data-source', required=True, help='price_history_v2 data_source to read.')
    parser.add_argument('--top-n', type=int, default=10, help='Number of incumbent candidates to select. Defaults to 10.')
    parser.add_argument('--out-dir', required=True, help='Output directory for incumbent screener report files.')
    parser.add_argument('--universe-db-path', help='Optional read-only v1 reference DB for universe_cache.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    kwargs = {
        'db_path': Path(args.db_path),
        'universe_id': args.universe_id,
        'benchmark_ticker': args.benchmark_ticker,
        'as_of_date': date.fromisoformat(args.as_of_date),
        'data_source': args.data_source,
        'top_n': args.top_n,
    }
    if args.universe_db_path:
        kwargs['universe_db_path'] = Path(args.universe_db_path)
    result = build_incumbent_screener(**kwargs)
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_incumbent_screener_outputs(result=result, out_dir=out_dir)
    print(f'{result.baseline_id} incumbent screener report written to {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

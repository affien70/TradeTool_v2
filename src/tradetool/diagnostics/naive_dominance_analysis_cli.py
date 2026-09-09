from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from tradetool.diagnostics.naive_dominance_analysis import (
    build_naive_dominance_analysis,
    write_naive_dominance_analysis_outputs,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Investigate naive baseline dominance without tuning.')
    parser.add_argument('--db-path', required=True, help='Temporary price_history_v2 SQLite DB path to read.')
    parser.add_argument('--universe-id', required=True, help='Universe ID to load from read-only universe_cache.')
    parser.add_argument('--benchmark-ticker', required=True, help='Benchmark ticker for forward excess returns.')
    parser.add_argument('--rebalance-start-date', required=True, help='First rebalance date boundary in YYYY-MM-DD format.')
    parser.add_argument('--rebalance-end-date', required=True, help='Last rebalance date boundary in YYYY-MM-DD format.')
    parser.add_argument('--data-source', required=True, help='price_history_v2 data_source to read.')
    parser.add_argument('--windows', nargs='+', type=int, default=[20, 60], help='Forward windows in trading rows. Defaults to 20 60.')
    parser.add_argument('--out-dir', required=True, help='Output directory for naive dominance analysis report files.')
    parser.add_argument('--universe-db-path', help='Optional read-only v1 reference DB for universe_cache.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    kwargs = {
        'db_path': Path(args.db_path),
        'universe_id': args.universe_id,
        'benchmark_ticker': args.benchmark_ticker,
        'rebalance_start_date': date.fromisoformat(args.rebalance_start_date),
        'rebalance_end_date': date.fromisoformat(args.rebalance_end_date),
        'data_source': args.data_source,
        'windows': tuple(args.windows),
    }
    if args.universe_db_path:
        kwargs['universe_db_path'] = Path(args.universe_db_path)
    result = build_naive_dominance_analysis(**kwargs)
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_naive_dominance_analysis_outputs(result=result, out_dir=out_dir)
    print(f'{result.decision_recommendation} naive dominance analysis written to {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

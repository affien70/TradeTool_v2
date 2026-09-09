from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from tradetool.diagnostics.candidate_quality_comparison import DEFAULT_REFERENCE_DB_PATH
from tradetool.diagnostics.holdout_snapshot import build_holdout_snapshot, write_holdout_snapshot_outputs


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Build one historical v2 holdout snapshot without forward returns.')
    parser.add_argument('--db-path', required=True, help='Temporary price_history_v2 SQLite DB path to read.')
    parser.add_argument('--universe-id', required=True, help='Universe ID to load from read-only universe_cache.')
    parser.add_argument('--benchmark-ticker', required=True, help='Benchmark ticker capped at the same as-of date.')
    parser.add_argument('--as-of-date', required=True, help='Historical snapshot date in YYYY-MM-DD format.')
    parser.add_argument('--data-source', required=True, help='price_history_v2 data_source to read.')
    parser.add_argument('--out-dir', required=True, help='Output directory for snapshot report files.')
    parser.add_argument(
        '--universe-db-path',
        default=str(DEFAULT_REFERENCE_DB_PATH),
        help='Read-only v1 reference DB used only for universe_cache.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    result = build_holdout_snapshot(
        db_path=Path(args.db_path),
        universe_id=args.universe_id,
        benchmark_ticker=args.benchmark_ticker,
        as_of_date=date.fromisoformat(args.as_of_date),
        data_source=args.data_source,
        universe_db_path=Path(args.universe_db_path),
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_holdout_snapshot_outputs(result=result, out_dir=out_dir)
    status = 'VALID' if result.snapshot_valid else 'INVALID'
    print(f'{status} holdout snapshot written to {out_dir}')
    return 0 if result.snapshot_valid else 1


if __name__ == '__main__':
    raise SystemExit(main())

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from tradetool.diagnostics.candidate_quality_comparison import (
    DEFAULT_REFERENCE_DB_PATH,
    build_candidate_quality_comparison,
    remove_temp_db_if_allowed,
    write_candidate_quality_outputs,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run current-Yahoo v2 candidate-quality comparison using a temp DB.')
    parser.add_argument('--db-path', required=True, help='Explicit temporary SQLite DB path.')
    parser.add_argument('--universe-id', required=True, help='Universe ID to load from read-only universe_cache.')
    parser.add_argument('--benchmark-ticker', required=True, help='Benchmark ticker, for example OSEBX.OL.')
    parser.add_argument('--start-date', required=True, help='Inclusive Yahoo start date in YYYY-MM-DD format.')
    parser.add_argument('--end-date', help='Inclusive end date in YYYY-MM-DD format. Defaults to today.')
    parser.add_argument('--source', required=True, help='Market-data source name, for example yahoo.')
    parser.add_argument('--allow-test-db-write', action='store_true', help='Required acknowledgement for temp DB writes.')
    parser.add_argument(
        '--allow-partial-invalid-skip',
        action='store_true',
        help='Write valid rows to the temp DB while reporting invalid skipped rows.',
    )
    parser.add_argument(
        '--universe-db-path',
        default=str(DEFAULT_REFERENCE_DB_PATH),
        help='Read-only v1 reference DB used only for universe_cache.',
    )
    parser.add_argument('--out-dir', required=True, help='Output directory for candidate quality report files.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    if not args.allow_test_db_write:
        parser.error('--allow-test-db-write is required for temporary market-data writes.')
    db_path = Path(args.db_path).expanduser().resolve()
    remove_temp_db_if_allowed(db_path)
    try:
        result = build_candidate_quality_comparison(
            db_path=db_path,
            universe_id=args.universe_id,
            benchmark_ticker=args.benchmark_ticker,
            start_date=date.fromisoformat(args.start_date),
            end_date=None if args.end_date is None else date.fromisoformat(args.end_date),
            source_name=args.source,
            allow_test_db_write=True,
            allow_partial_invalid_skip=args.allow_partial_invalid_skip,
            universe_db_path=Path(args.universe_db_path),
        )
        write_candidate_quality_outputs(result=result, out_dir=Path(args.out_dir))
    finally:
        remove_temp_db_if_allowed(db_path)
    print(f'VALID candidate quality comparison written to {Path(args.out_dir).expanduser().resolve()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

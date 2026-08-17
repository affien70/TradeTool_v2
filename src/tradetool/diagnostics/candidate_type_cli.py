from __future__ import annotations

import argparse
from pathlib import Path

from tradetool.diagnostics.candidate_type import build_candidate_type_diagnostics, write_candidate_type_outputs


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Generate read-only candidate-type diagnostics from a SQLite copy.')
    parser.add_argument('--db-path', required=True, help='Path to a local SQLite copy.')
    parser.add_argument('--universe-id', required=True, help='Universe identifier for the candidate-type report.')
    parser.add_argument('--benchmark-ticker', help='Optional benchmark ticker.')
    parser.add_argument('--out-dir', required=True, help='Output directory for the generated candidate-type files.')
    parser.add_argument('--universe-csv', help='Optional CSV file containing a ticker column.')
    parser.add_argument('--tickers', help='Optional comma-separated ticker list.')
    parser.add_argument('--price-table', default='price_history', help='Explicit price-history table name.')
    parser.add_argument('--min-history-rows', type=int, default=252, help='Minimum required row count per ticker for structural eligibility.')
    parser.add_argument('--freshness-tolerance-days', type=int, default=0, help='Allowed lag behind the universe max price date.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    explicit_tickers = None
    if args.tickers:
        explicit_tickers = [ticker.strip() for ticker in args.tickers.split(',')]
    result = build_candidate_type_diagnostics(
        db_path=Path(args.db_path),
        universe_id=args.universe_id,
        benchmark_ticker=args.benchmark_ticker,
        explicit_tickers=explicit_tickers,
        universe_csv_path=args.universe_csv,
        price_table=args.price_table,
        min_history_rows=args.min_history_rows,
        freshness_tolerance_days=args.freshness_tolerance_days,
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_candidate_type_outputs(result=result, out_dir=out_dir)
    print(f'VALID candidate type diagnostics written to {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

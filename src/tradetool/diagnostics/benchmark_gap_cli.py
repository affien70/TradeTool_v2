from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from tradetool.diagnostics.benchmark_gap import build_benchmark_gap_report, write_benchmark_gap_outputs


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Compare Yahoo benchmark source coverage and stock alignment without DB access.')
    parser.add_argument('--tickers', nargs='+', required=True, help='Explicit stock tickers to compare against benchmarks.')
    parser.add_argument('--benchmark-candidates', nargs='+', required=True, help='Explicit benchmark candidate tickers.')
    parser.add_argument('--start-date', required=True, help='Inclusive start date in YYYY-MM-DD format.')
    parser.add_argument('--end-date', help='Inclusive end date in YYYY-MM-DD format. Defaults to today.')
    parser.add_argument('--source', required=True, help='Market-data source name, for example yahoo.')
    parser.add_argument('--out-dir', required=True, help='Output directory for generated benchmark gap report files.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    start_date = date.fromisoformat(args.start_date)
    end_date = date.today() if args.end_date is None else date.fromisoformat(args.end_date)
    result = build_benchmark_gap_report(
        tickers=list(args.tickers),
        benchmark_candidates=list(args.benchmark_candidates),
        start_date=start_date,
        end_date=end_date,
        source_name=args.source,
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_benchmark_gap_outputs(result=result, out_dir=out_dir)
    print(f'{result.recommendation.recommendation} benchmark gap diagnostics written to {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

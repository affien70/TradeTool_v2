from __future__ import annotations

import argparse
from pathlib import Path

from tradetool.diagnostics.holdout_backtest_feasibility import (
    build_holdout_backtest_feasibility,
    write_holdout_backtest_feasibility_outputs,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Inspect holdout/backtest feasibility without running a backtest.')
    parser.add_argument('--universe-id', required=True, help='Universe ID for the proposed comparison.')
    parser.add_argument('--benchmark-ticker', required=True, help='Benchmark ticker for the proposed comparison.')
    parser.add_argument('--out-dir', required=True, help='Output directory for feasibility report files.')
    parser.add_argument('--project-root', default='.', help='Project root to inspect. Defaults to current directory.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    result = build_holdout_backtest_feasibility(
        universe_id=args.universe_id,
        benchmark_ticker=args.benchmark_ticker,
        project_root=Path(args.project_root),
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_holdout_backtest_feasibility_outputs(result=result, out_dir=out_dir)
    print(f'{result.decision_recommendation} holdout/backtest feasibility written to {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

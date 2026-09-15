from __future__ import annotations

import argparse
from pathlib import Path

from tradetool.diagnostics.cross_universe_decision import (
    build_cross_universe_decision,
    write_cross_universe_decision_outputs,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Write the Phase 5m cross-universe incumbent decision report.')
    parser.add_argument('--out-dir', required=True, help='Output directory for cross-universe decision report files.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    result = build_cross_universe_decision()
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_cross_universe_decision_outputs(result=result, out_dir=out_dir)
    print(f'{result.decision_recommendation} cross-universe decision written to {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

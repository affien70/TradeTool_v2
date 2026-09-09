from __future__ import annotations

import argparse
from pathlib import Path

from tradetool.diagnostics.incumbent_baseline import (
    build_incumbent_baseline_definition,
    write_incumbent_baseline_outputs,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Write the explicit v2 incumbent testing baseline definition.')
    parser.add_argument('--out-dir', required=True, help='Output directory for incumbent baseline definition files.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    result = build_incumbent_baseline_definition()
    out_dir = Path(args.out_dir).expanduser().resolve()
    write_incumbent_baseline_outputs(result=result, out_dir=out_dir)
    print(f'{result.decision_recommendation} incumbent baseline definition written to {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

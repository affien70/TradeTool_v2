from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from pathlib import Path

from tradetool.config.runtime_settings import get_app_database_path
from tradetool.data.sqlite_readonly import ReadOnlySQLite
from tradetool.diagnostics.incumbent_candidate_quality_audit import (
    build_candidate_quality_audit,
    write_candidate_quality_audit,
)

BENCHMARKS = {'NORWAY_V2': 'OSEBX.OL', 'SP500': '^GSPC'}


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Audit incumbent Top 10/20/30 candidate quality without changing selection.')
    parser.add_argument('--db-path', help='Read-only app SQLite database; defaults to configured local app DB.')
    parser.add_argument('--universe-id', required=True, choices=tuple(BENCHMARKS))
    parser.add_argument('--as-of-date', help='YYYY-MM-DD; defaults to latest price date in the chosen data source.')
    parser.add_argument('--data-source', default='yahoo')
    parser.add_argument('--out-dir', help='New report directory; defaults to an append-only timestamped directory.')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    db_path = Path(args.db_path).expanduser().resolve() if args.db_path else get_app_database_path()
    as_of_date = date.fromisoformat(args.as_of_date) if args.as_of_date else _latest_price_date(db_path, args.data_source)
    result = build_candidate_quality_audit(
        db_path=db_path,
        universe_id=args.universe_id,
        benchmark_ticker=BENCHMARKS[args.universe_id],
        as_of_date=as_of_date,
        data_source=args.data_source,
    )
    out_dir = Path(args.out_dir) if args.out_dir else Path('reports/incumbent_candidate_quality') / (
        f"{args.universe_id.lower()}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    write_candidate_quality_audit(result=result, out_dir=out_dir)
    print(f"{args.universe_id}: ranked={result.summary['eligible_ranked_count']} "
          f"top10_HIGH={result.summary['top_n']['10']['risk_level_counts']['HIGH']} "
          f"report={out_dir.expanduser().resolve()}")
    return 0


def _latest_price_date(db_path: Path, data_source: str) -> date:
    database = ReadOnlySQLite(db_path)
    row = database.fetch_one(
        'SELECT MAX(price_date) AS latest_date FROM price_history_v2 WHERE data_source = ?',
        (data_source,),
    )
    if row is None or row['latest_date'] is None:
        raise ValueError(f'No price_history_v2 rows found for data source {data_source!r}.')
    return date.fromisoformat(str(row['latest_date']))


if __name__ == '__main__':
    raise SystemExit(main())

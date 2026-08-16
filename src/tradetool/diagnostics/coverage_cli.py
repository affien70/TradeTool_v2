from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
import json
from pathlib import Path

from tradetool.diagnostics.coverage import build_coverage_diagnostics


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Generate read-only TradeTool v2 coverage diagnostics from a SQLite copy.')
    parser.add_argument('--db-path', required=True, help='Path to a local SQLite copy.')
    parser.add_argument('--universe-id', required=True, help='Universe identifier for the coverage report.')
    parser.add_argument('--out-dir', required=True, help='Output directory for the generated coverage report files.')
    parser.add_argument('--universe-csv', help='Optional CSV file containing a ticker column.')
    parser.add_argument('--tickers', help='Optional comma-separated ticker list.')
    parser.add_argument('--price-table', help='Optional explicit price-history table name.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    explicit_tickers = None
    if args.tickers:
        explicit_tickers = [ticker.strip() for ticker in args.tickers.split(',')]
    result = build_coverage_diagnostics(
        db_path=Path(args.db_path),
        universe_id=args.universe_id,
        explicit_tickers=explicit_tickers,
        universe_csv_path=args.universe_csv,
        price_table=args.price_table,
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_outputs(result=result, out_dir=out_dir)
    print(f'VALID coverage diagnostics written to {out_dir}')
    return 0


def _write_outputs(*, result, out_dir: Path) -> None:
    (out_dir / 'coverage_report.json').write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'coverage_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')
    with (out_dir / 'latest_data_date_distribution.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['latest_data_date', 'ticker_count'])
        writer.writeheader()
        for latest_data_date, ticker_count in result.latest_data_date_distribution_rows:
            writer.writerow({'latest_data_date': latest_data_date, 'ticker_count': ticker_count})


def _render_summary_markdown(result) -> str:
    report = result.report
    timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%SZ')
    lines = [
        '# Coverage Summary',
        '',
        f'- Generated: {timestamp}',
        f'- Universe: {result.universe_id}',
        f'- Source DB: `{result.db_path}`',
        f'- Price table: `{result.price_table}`',
        '',
        '## Core counts',
        '',
        f'- Input universe count: {report.input_universe_count}',
        f'- Valid ticker count: {report.valid_ticker_count}',
        f'- Market-data coverage count: {report.market_data_coverage_count}',
        f'- Enough-history count (>=252 rows): {report.enough_history_count}',
        f'- Feature-complete placeholder count: {report.feature_complete_count}',
        f'- Eligible placeholder count: {report.eligible_count}',
        f'- Ranked count placeholder: {report.ranked_count}',
        '',
        '## Data quality',
        '',
        f'- Total rows: {result.row_count}',
        f'- Min price date: {result.min_price_date or "n/a"}',
        f'- Max price date: {result.max_price_date or "n/a"}',
        f'- Tickers with >=504 rows: {result.tickers_with_at_least_504_rows}',
        f'- Fresh latest-data count: {result.fresh_latest_data_count}',
        f'- Duplicate ticker/date rows: {result.duplicate_ticker_date_rows}',
        f'- Missing or invalid rows: {result.missing_or_invalid_row_count}',
        f'- Invalid date values: {result.invalid_date_value_count}',
        '',
        '## Rejection counts',
        '',
    ]
    for reason, count in sorted(report.rejection_counts_by_reason.items()):
        lines.append(f'- {reason}: {count}')
    lines.extend(['', '## Notes', ''])
    for note in report.benchmark_coverage_notes:
        lines.append(f'- {note}')
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    raise SystemExit(main())

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
import json
from pathlib import Path

from tradetool.diagnostics.eligibility import build_eligibility_diagnostics


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Generate read-only structural eligibility diagnostics from a SQLite copy.')
    parser.add_argument('--db-path', required=True, help='Path to a local SQLite copy.')
    parser.add_argument('--universe-id', required=True, help='Universe identifier for the eligibility report.')
    parser.add_argument('--out-dir', required=True, help='Output directory for the generated eligibility files.')
    parser.add_argument('--universe-csv', help='Optional CSV file containing a ticker column.')
    parser.add_argument('--tickers', help='Optional comma-separated ticker list.')
    parser.add_argument('--price-table', help='Optional explicit price-history table name.')
    parser.add_argument('--min-history-rows', type=int, default=252, help='Minimum required row count per ticker.')
    parser.add_argument('--freshness-tolerance-days', type=int, default=0, help='Allowed lag behind the universe max price date.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    explicit_tickers = None
    if args.tickers:
        explicit_tickers = [ticker.strip() for ticker in args.tickers.split(',')]
    result = build_eligibility_diagnostics(
        db_path=Path(args.db_path),
        universe_id=args.universe_id,
        explicit_tickers=explicit_tickers,
        universe_csv_path=args.universe_csv,
        price_table=args.price_table,
        min_history_rows=args.min_history_rows,
        freshness_tolerance_days=args.freshness_tolerance_days,
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_outputs(result=result, out_dir=out_dir)
    print(f'VALID structural eligibility diagnostics written to {out_dir}')
    return 0


def _write_outputs(*, result, out_dir: Path) -> None:
    with (out_dir / 'eligibility_results.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                'ticker',
                'eligible',
                'rejection_reasons',
                'row_count',
                'latest_price_date',
                'universe_max_price_date',
                'has_market_data',
                'has_duplicate_rows',
                'has_invalid_ohlc',
                'source_universe',
            ],
        )
        writer.writeheader()
        for row in result.results:
            writer.writerow(
                {
                    'ticker': row.ticker,
                    'eligible': row.eligible,
                    'rejection_reasons': '|'.join(row.rejection_reasons),
                    'row_count': row.row_count,
                    'latest_price_date': row.latest_price_date or '',
                    'universe_max_price_date': row.universe_max_price_date or '',
                    'has_market_data': row.has_market_data,
                    'has_duplicate_rows': row.has_duplicate_rows,
                    'has_invalid_ohlc': row.has_invalid_ohlc,
                    'source_universe': row.source_universe,
                }
            )
    (out_dir / 'eligibility_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'eligibility_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')


def _render_summary_markdown(result) -> str:
    timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%SZ')
    lines = [
        '# Structural Eligibility Summary',
        '',
        f'- Generated: {timestamp}',
        f'- Universe: {result.universe_id}',
        f'- Universe source: {result.universe_source}',
        f'- Source DB: `{result.db_path}`',
        f'- Price table: `{result.price_table}`',
        '',
        '## Counts',
        '',
        f'- Input universe count: {result.input_universe_count}',
        f'- Eligible count: {result.eligible_count}',
        f'- Rejected count: {result.rejected_count}',
        f'- Minimum history rows: {result.min_history_rows}',
        f'- Max price date: {result.max_price_date or "n/a"}',
        '',
        '## Rejection counts',
        '',
    ]
    for reason, count in sorted(result.rejection_counts_by_reason.items()):
        lines.append(f'- {reason}: {count}')
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    raise SystemExit(main())

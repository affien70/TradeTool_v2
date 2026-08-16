from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
import json
from pathlib import Path

from tradetool.diagnostics.baseline_ranking import build_baseline_ranking_diagnostics


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Generate read-only baseline ranking diagnostics from a SQLite copy.')
    parser.add_argument('--db-path', required=True, help='Path to a local SQLite copy.')
    parser.add_argument('--universe-id', required=True, help='Universe identifier for the baseline ranking report.')
    parser.add_argument('--benchmark-ticker', help='Optional benchmark ticker.')
    parser.add_argument('--out-dir', required=True, help='Output directory for the generated baseline ranking files.')
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
    result = build_baseline_ranking_diagnostics(
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
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_outputs(result=result, out_dir=out_dir)
    print(f'VALID baseline ranking diagnostics written to {out_dir}')
    return 0


def _write_outputs(*, result, out_dir: Path) -> None:
    fieldnames = list(result.rows[0].to_dict().keys()) if result.rows else [
        'ticker', 'rank_date', 'ranking_engine_id', 'raw_rank', 'raw_score'
    ]
    with (out_dir / 'baseline_ranking.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in result.rows:
            writer.writerow(row.to_dict())
    (out_dir / 'baseline_ranking_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'baseline_ranking_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')


def _render_summary_markdown(result) -> str:
    timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%SZ')
    lines = [
        '# Baseline Ranking Summary',
        '',
        f'- Generated: {timestamp}',
        f'- Universe: {result.universe_id}',
        f'- Universe source: {result.universe_source}',
        f'- Benchmark: {result.benchmark_ticker or "not requested"}',
        f'- Ranking engine id: {result.ranking_engine_id}',
        f'- Source DB: `{result.db_path}`',
        '',
        '## Counts',
        '',
        f'- Input universe count: {result.input_universe_count}',
        f'- Structural eligible count: {result.structural_eligible_count}',
        f'- Structural rejected count: {result.structural_rejected_count}',
        f'- Feature complete count: {result.feature_complete_count}',
        f'- Feature incomplete count: {result.feature_incomplete_count}',
        f'- Ranked count: {result.ranked_count}',
    ]
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    raise SystemExit(main())

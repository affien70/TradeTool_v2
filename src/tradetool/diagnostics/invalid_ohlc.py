from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from tradetool.data import ReadOnlySQLite
from tradetool.diagnostics.coverage import _coerce_optional_date, _resolve_column_name, _resolve_price_table
from tradetool.universe import load_universe_tickers

_DATE_COLUMN_CANDIDATES = ('date', 'trade_date', 'price_date', 'as_of_date')
_REQUIRED_PRICE_COLUMNS = ('ticker', 'open', 'high', 'low', 'close', 'volume')
_RECENT_GAP_DAYS = 30
_LATEST_WINDOW_ROWS = 252


@dataclass(frozen=True, slots=True)
class InvalidOhlcRow:
    ticker: str
    price_date: str | None
    raw_price_date: str | None
    open_value: float | None
    high_value: float | None
    low_value: float | None
    close_value: float | None
    volume_value: float | None
    reason_codes: tuple[str, ...]
    days_behind_universe_max: int | None
    recency_bucket: str
    within_latest_252_rows: bool | None

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'price_date': self.price_date,
            'raw_price_date': self.raw_price_date,
            'open': self.open_value,
            'high': self.high_value,
            'low': self.low_value,
            'close': self.close_value,
            'volume': self.volume_value,
            'reason_codes': '; '.join(self.reason_codes),
            'days_behind_universe_max': self.days_behind_universe_max,
            'recency_bucket': self.recency_bucket,
            'within_latest_252_rows': self.within_latest_252_rows,
        }


@dataclass(frozen=True, slots=True)
class InvalidOhlcByTickerRow:
    ticker: str
    invalid_row_count: int
    reason_code_count: int
    first_invalid_date: str | None
    latest_invalid_date: str | None
    invalid_rows_within_latest_252: int
    invalid_rows_within_30_days_of_universe_max: int
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'invalid_row_count': self.invalid_row_count,
            'reason_code_count': self.reason_code_count,
            'first_invalid_date': self.first_invalid_date,
            'latest_invalid_date': self.latest_invalid_date,
            'invalid_rows_within_latest_252': self.invalid_rows_within_latest_252,
            'invalid_rows_within_30_days_of_universe_max': self.invalid_rows_within_30_days_of_universe_max,
            'reason_codes': '; '.join(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class InvalidOhlcByReasonRow:
    reason_code: str
    invalid_row_count: int
    affected_ticker_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            'reason_code': self.reason_code,
            'invalid_row_count': self.invalid_row_count,
            'affected_ticker_count': self.affected_ticker_count,
        }


@dataclass(frozen=True, slots=True)
class InvalidOhlcDiagnosticsResult:
    db_path: Path
    universe_id: str
    universe_source: str
    detected_price_table: str
    price_date_column: str
    universe_ticker_count: int
    universe_max_price_date: str | None
    invalid_row_count: int
    affected_ticker_count: int
    rows_with_recent_invalid_dates: int
    rows_with_historical_invalid_dates: int
    rows_on_universe_max_date: int
    rows_within_latest_252_window: int
    tickers_within_latest_252_window: int
    likely_harmless_for_latest_252_window: bool
    concentration_status: str
    top_10_ticker_share_of_invalid_rows: float
    generated_at_utc: str
    rows: tuple[InvalidOhlcRow, ...]
    by_ticker_rows: tuple[InvalidOhlcByTickerRow, ...]
    by_reason_rows: tuple[InvalidOhlcByReasonRow, ...]

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'db_path': str(self.db_path),
            'universe_id': self.universe_id,
            'universe_source': self.universe_source,
            'detected_price_table': self.detected_price_table,
            'price_date_column': self.price_date_column,
            'universe_ticker_count': self.universe_ticker_count,
            'universe_max_price_date': self.universe_max_price_date,
            'invalid_row_count': self.invalid_row_count,
            'affected_ticker_count': self.affected_ticker_count,
            'rows_with_recent_invalid_dates': self.rows_with_recent_invalid_dates,
            'rows_with_historical_invalid_dates': self.rows_with_historical_invalid_dates,
            'rows_on_universe_max_date': self.rows_on_universe_max_date,
            'rows_within_latest_252_window': self.rows_within_latest_252_window,
            'tickers_within_latest_252_window': self.tickers_within_latest_252_window,
            'likely_harmless_for_latest_252_window': self.likely_harmless_for_latest_252_window,
            'concentration_status': self.concentration_status,
            'top_10_ticker_share_of_invalid_rows': self.top_10_ticker_share_of_invalid_rows,
            'reason_distribution': [row.to_dict() for row in self.by_reason_rows],
            'generated_at_utc': self.generated_at_utc,
        }


def build_invalid_ohlc_diagnostics(
    *,
    db_path: str | Path,
    universe_id: str,
    price_table: str | None = None,
) -> InvalidOhlcDiagnosticsResult:
    resolved_db_path = Path(db_path).expanduser().resolve()
    database = ReadOnlySQLite(resolved_db_path)
    schema = database.inspect_schema()
    resolved_price_table = price_table or _resolve_price_table(schema)
    ticker_column = _resolve_column_name(schema, resolved_price_table, ('ticker', 'symbol', 'ric'), 'ticker')
    price_date_column = _resolve_optional_column_name(schema, resolved_price_table, _DATE_COLUMN_CANDIDATES)
    if price_date_column is None:
        raise ValueError(f'Unable to detect a date column for table "{resolved_price_table}".')

    table_columns = {
        column.name.lower(): column.name
        for column in schema.columns_by_table.get(resolved_price_table, ())
    }
    missing_columns = [column for column in _REQUIRED_PRICE_COLUMNS if column not in table_columns]
    if missing_columns:
        raise ValueError(f'Missing required price columns for invalid OHLC diagnostics: {", ".join(missing_columns)}')

    universe = load_universe_tickers(
        universe_id=universe_id,
        database=database,
        price_table=resolved_price_table,
        ticker_column=ticker_column,
    )
    relevant_tickers = set(universe.tickers)
    all_rows = _fetch_relevant_price_rows(
        database=database,
        table_name=resolved_price_table,
        ticker_column=ticker_column,
        date_column=price_date_column,
        open_column=table_columns['open'],
        high_column=table_columns['high'],
        low_column=table_columns['low'],
        close_column=table_columns['close'],
        volume_column=table_columns['volume'],
        relevant_tickers=relevant_tickers,
    )
    latest_positions = _latest_row_positions_by_ticker(all_rows)
    valid_dates = [row['price_date'] for row in all_rows if row['price_date'] is not None]
    universe_max_price_date = max(valid_dates) if valid_dates else None

    invalid_rows: list[InvalidOhlcRow] = []
    per_ticker_rows: defaultdict[str, list[InvalidOhlcRow]] = defaultdict(list)
    reason_counter: Counter[str] = Counter()
    reason_tickers: defaultdict[str, set[str]] = defaultdict(set)
    for row in all_rows:
        reason_codes = _classify_invalid_ohlc_reasons(row)
        if not reason_codes:
            continue
        days_behind = None
        recency_bucket = 'unknown_date'
        if universe_max_price_date is not None and row['price_date'] is not None:
            days_behind = (universe_max_price_date - row['price_date']).days
            if days_behind == 0:
                recency_bucket = 'on_universe_max_date'
            elif days_behind <= _RECENT_GAP_DAYS:
                recency_bucket = 'within_30_days_of_universe_max'
            else:
                recency_bucket = 'older_than_30_days'
        latest_position = latest_positions.get((row['ticker'], row['price_date'], row['raw_price_date']))
        invalid_row = InvalidOhlcRow(
            ticker=row['ticker'],
            price_date=None if row['price_date'] is None else row['price_date'].isoformat(),
            raw_price_date=row['raw_price_date'],
            open_value=row['open'],
            high_value=row['high'],
            low_value=row['low'],
            close_value=row['close'],
            volume_value=row['volume'],
            reason_codes=reason_codes,
            days_behind_universe_max=days_behind,
            recency_bucket=recency_bucket,
            within_latest_252_rows=None if latest_position is None else latest_position <= _LATEST_WINDOW_ROWS,
        )
        invalid_rows.append(invalid_row)
        per_ticker_rows[invalid_row.ticker].append(invalid_row)
        reason_counter.update(reason_codes)
        for reason_code in reason_codes:
            reason_tickers[reason_code].add(invalid_row.ticker)

    by_ticker_rows = tuple(
        sorted(
            (
                InvalidOhlcByTickerRow(
                    ticker=ticker,
                    invalid_row_count=len(rows),
                    reason_code_count=sum(len(row.reason_codes) for row in rows),
                    first_invalid_date=_min_date_string(rows),
                    latest_invalid_date=_max_date_string(rows),
                    invalid_rows_within_latest_252=sum(1 for row in rows if row.within_latest_252_rows is True),
                    invalid_rows_within_30_days_of_universe_max=sum(1 for row in rows if row.recency_bucket in {'on_universe_max_date', 'within_30_days_of_universe_max'}),
                    reason_codes=tuple(sorted({reason for row in rows for reason in row.reason_codes})),
                )
                for ticker, rows in per_ticker_rows.items()
            ),
            key=lambda row: (-row.invalid_row_count, row.ticker),
        )
    )
    by_reason_rows = tuple(
        InvalidOhlcByReasonRow(
            reason_code=reason_code,
            invalid_row_count=count,
            affected_ticker_count=len(reason_tickers[reason_code]),
        )
        for reason_code, count in sorted(reason_counter.items(), key=lambda item: (-item[1], item[0]))
    )
    rows_with_recent_invalid_dates = sum(
        1 for row in invalid_rows if row.recency_bucket in {'on_universe_max_date', 'within_30_days_of_universe_max'}
    )
    rows_on_universe_max_date = sum(1 for row in invalid_rows if row.recency_bucket == 'on_universe_max_date')
    rows_with_historical_invalid_dates = sum(1 for row in invalid_rows if row.recency_bucket == 'older_than_30_days')
    rows_within_latest_252_window = sum(1 for row in invalid_rows if row.within_latest_252_rows is True)
    tickers_within_latest_252_window = len({row.ticker for row in invalid_rows if row.within_latest_252_rows is True})
    top_10_total = sum(row.invalid_row_count for row in by_ticker_rows[:10])
    top_10_share = 0.0 if not invalid_rows else top_10_total / len(invalid_rows)
    concentration_status = 'concentrated_in_few_tickers' if top_10_share >= 0.5 else 'spread_across_many_tickers'

    return InvalidOhlcDiagnosticsResult(
        db_path=resolved_db_path,
        universe_id=universe_id,
        universe_source=universe.source,
        detected_price_table=resolved_price_table,
        price_date_column=price_date_column,
        universe_ticker_count=len(universe.tickers),
        universe_max_price_date=None if universe_max_price_date is None else universe_max_price_date.isoformat(),
        invalid_row_count=len(invalid_rows),
        affected_ticker_count=len(per_ticker_rows),
        rows_with_recent_invalid_dates=rows_with_recent_invalid_dates,
        rows_with_historical_invalid_dates=rows_with_historical_invalid_dates,
        rows_on_universe_max_date=rows_on_universe_max_date,
        rows_within_latest_252_window=rows_within_latest_252_window,
        tickers_within_latest_252_window=tickers_within_latest_252_window,
        likely_harmless_for_latest_252_window=rows_within_latest_252_window == 0,
        concentration_status=concentration_status,
        top_10_ticker_share_of_invalid_rows=top_10_share,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        rows=tuple(sorted(invalid_rows, key=lambda row: (row.ticker, row.price_date or '', ';'.join(row.reason_codes)))),
        by_ticker_rows=by_ticker_rows,
        by_reason_rows=by_reason_rows,
    )


def write_invalid_ohlc_outputs(*, result: InvalidOhlcDiagnosticsResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / 'invalid_ohlc_rows.csv', [row.to_dict() for row in result.rows])
    _write_csv(out_dir / 'invalid_ohlc_by_ticker.csv', [row.to_dict() for row in result.by_ticker_rows])
    _write_csv(out_dir / 'invalid_ohlc_by_reason.csv', [row.to_dict() for row in result.by_reason_rows])
    (out_dir / 'invalid_ohlc_summary.json').write_text(
        json.dumps(result.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'invalid_ohlc_summary.md').write_text(_render_summary_markdown(result), encoding='utf-8')


def _resolve_optional_column_name(schema, table_name: str, candidates: Sequence[str]) -> str | None:
    columns = schema.columns_by_table.get(table_name)
    if columns is None:
        return None
    normalized = {column.name.lower(): column.name for column in columns}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def _fetch_relevant_price_rows(
    *,
    database: ReadOnlySQLite,
    table_name: str,
    ticker_column: str,
    date_column: str,
    open_column: str,
    high_column: str,
    low_column: str,
    close_column: str,
    volume_column: str,
    relevant_tickers: set[str],
) -> list[dict[str, object]]:
    quoted_ticker = f'"{ticker_column}"'
    placeholders = ', '.join('?' for _ in relevant_tickers)
    # Keep one scoped read for all relevant rows.
    scoped_rows = database.fetch_all(
        f"""
        SELECT
            UPPER(TRIM({quoted_ticker})) AS ticker,
            "{date_column}" AS raw_price_date,
            date("{date_column}") AS normalized_price_date,
            "{open_column}" AS open_value,
            "{high_column}" AS high_value,
            "{low_column}" AS low_value,
            "{close_column}" AS close_value,
            "{volume_column}" AS volume_value
        FROM "{table_name}"
        WHERE {quoted_ticker} IS NOT NULL
          AND TRIM({quoted_ticker}) <> ''
          AND UPPER(TRIM({quoted_ticker})) IN ({placeholders})
        ORDER BY UPPER(TRIM({quoted_ticker})), date("{date_column}"), "{date_column}"
        """,
        tuple(sorted(relevant_tickers)),
    )
    return [
        {
            'ticker': str(row['ticker']),
            'raw_price_date': None if row['raw_price_date'] is None else str(row['raw_price_date']),
            'price_date': _coerce_optional_date(row['normalized_price_date']),
            'open': _coerce_optional_float(row['open_value']),
            'high': _coerce_optional_float(row['high_value']),
            'low': _coerce_optional_float(row['low_value']),
            'close': _coerce_optional_float(row['close_value']),
            'volume': _coerce_optional_float(row['volume_value']),
        }
        for row in scoped_rows
    ]


def _latest_row_positions_by_ticker(rows: Sequence[Mapping[str, object]]) -> dict[tuple[str, date | None, str | None], int]:
    by_ticker: defaultdict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        by_ticker[str(row['ticker'])].append(row)
    positions: dict[tuple[str, date | None, str | None], int] = {}
    for ticker, ticker_rows in by_ticker.items():
        dated_rows = [row for row in ticker_rows if row['price_date'] is not None]
        dated_rows.sort(
            key=lambda row: (
                -date.toordinal(row['price_date']),
                '' if row['raw_price_date'] is None else str(row['raw_price_date']),
            )
        )
        for index, row in enumerate(dated_rows, start=1):
            positions[(ticker, row['price_date'], row['raw_price_date'])] = index
    return positions


def _classify_invalid_ohlc_reasons(row: Mapping[str, object]) -> tuple[str, ...]:
    reasons: list[str] = []
    open_value = row['open']
    high_value = row['high']
    low_value = row['low']
    close_value = row['close']
    volume_value = row['volume']

    if open_value is None:
        reasons.append('missing_open')
    if high_value is None:
        reasons.append('missing_high')
    if low_value is None:
        reasons.append('missing_low')
    if close_value is None:
        reasons.append('missing_close')
    if volume_value is None:
        reasons.append('missing_volume')

    if open_value is not None and open_value <= 0:
        reasons.append('zero_or_negative_open')
    if high_value is not None and high_value <= 0:
        reasons.append('zero_or_negative_high')
    if low_value is not None and low_value <= 0:
        reasons.append('zero_or_negative_low')
    if close_value is not None and close_value <= 0:
        reasons.append('zero_or_negative_close')
    if volume_value is not None and volume_value < 0:
        reasons.append('negative_volume')

    if high_value is not None and low_value is not None and high_value < low_value:
        reasons.append('high_lower_than_low')
    if high_value is not None and open_value is not None and high_value < open_value:
        reasons.append('high_lower_than_open')
    if high_value is not None and close_value is not None and high_value < close_value:
        reasons.append('high_lower_than_close')
    if low_value is not None and open_value is not None and low_value > open_value:
        reasons.append('low_higher_than_open')
    if low_value is not None and close_value is not None and low_value > close_value:
        reasons.append('low_higher_than_close')

    return tuple(sorted(set(reasons)))


def _coerce_optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _min_date_string(rows: Sequence[InvalidOhlcRow]) -> str | None:
    dates = [row.price_date for row in rows if row.price_date is not None]
    return min(dates) if dates else None


def _max_date_string(rows: Sequence[InvalidOhlcRow]) -> str | None:
    dates = [row.price_date for row in rows if row.price_date is not None]
    return max(dates) if dates else None


def _render_summary_markdown(result: InvalidOhlcDiagnosticsResult) -> str:
    lines = [
        '# Invalid OHLC Summary',
        '',
        '## Scope',
        '',
        f'- DB path: {result.db_path}',
        f'- Universe id: {result.universe_id}',
        f'- Universe source: {result.universe_source}',
        f'- Detected price table: {result.detected_price_table}',
        f'- Selected date column: {result.price_date_column}',
        f'- Universe ticker count: {result.universe_ticker_count}',
        f'- Universe max price date: {result.universe_max_price_date or "unknown"}',
        '',
        '## Invalid OHLC totals',
        '',
        f'- Invalid OHLC rows: {result.invalid_row_count}',
        f'- Affected tickers: {result.affected_ticker_count}',
        f'- Concentration status: {result.concentration_status}',
        f'- Top 10 ticker share of invalid rows: {result.top_10_ticker_share_of_invalid_rows:.3f}',
        '',
        '## Timing',
        '',
        f'- Rows on universe max date: {result.rows_on_universe_max_date}',
        f'- Rows within 30 days of universe max: {result.rows_with_recent_invalid_dates}',
        f'- Rows older than 30 days from universe max: {result.rows_with_historical_invalid_dates}',
        '',
        '## Latest 252-row impact',
        '',
        f'- Invalid rows within each ticker latest 252 rows: {result.rows_within_latest_252_window}',
        f'- Affected tickers within latest 252 rows: {result.tickers_within_latest_252_window}',
        f'- Likely harmless for latest 252-row screener window: {result.likely_harmless_for_latest_252_window}',
        '',
        '## Top reasons',
        '',
    ]
    for row in result.by_reason_rows[:10]:
        lines.append(f'- {row.reason_code}: {row.invalid_row_count} rows across {row.affected_ticker_count} tickers')
    return '\n'.join(lines) + '\n'


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = ['value']
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

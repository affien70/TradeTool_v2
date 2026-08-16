from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import csv
import json
from pathlib import Path

from tradetool.data import ReadOnlySQLite

TICKER_COLUMN_CANDIDATES = ('ticker', 'symbol', 'ric')
UNIVERSE_CACHE_TABLE = 'universe_cache'


@dataclass(frozen=True, slots=True)
class UniverseTickerSelection:
    universe_id: str
    source: str
    input_count: int
    tickers: tuple[str, ...]
    invalid_values: tuple[str, ...] = ()


def normalize_ticker(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().upper()
    return normalized or None


def load_universe_tickers(
    *,
    universe_id: str,
    explicit_tickers: Iterable[str] | None = None,
    csv_path: str | Path | None = None,
    database: ReadOnlySQLite | None = None,
    price_table: str | None = None,
    ticker_column: str = 'ticker',
) -> UniverseTickerSelection:
    if explicit_tickers is not None:
        return _selection_from_values(universe_id=universe_id, source='explicit_ticker_list', values=explicit_tickers)
    if csv_path is not None:
        return _selection_from_csv(universe_id=universe_id, csv_path=csv_path)
    if database is not None:
        cache_selection = _selection_from_universe_cache(database=database, universe_id=universe_id)
        if cache_selection is not None:
            return cache_selection
        if UNIVERSE_CACHE_TABLE in database.list_tables():
            raise ValueError(f'Universe "{universe_id}" was not found in universe_cache.')
    if database is not None and price_table is not None:
        rows = database.fetch_all(
            f'SELECT DISTINCT "{ticker_column}" AS ticker FROM "{price_table}" WHERE "{ticker_column}" IS NOT NULL ORDER BY "{ticker_column}"'
        )
        values = [str(row['ticker']) for row in rows]
        return _selection_from_values(universe_id=universe_id, source='price_history_distinct_tickers', values=values)
    raise ValueError('Universe selection requires explicit tickers, a CSV path, or database fallback inputs.')


def _selection_from_csv(*, universe_id: str, csv_path: str | Path) -> UniverseTickerSelection:
    path = Path(csv_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f'Universe CSV path does not exist: {path}')
    with path.open('r', encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError('Universe CSV is missing a header row.')
        header_lookup = {field.strip().lower(): field for field in reader.fieldnames}
        ticker_field = next((header_lookup[name] for name in TICKER_COLUMN_CANDIDATES if name in header_lookup), None)
        if ticker_field is None:
            raise ValueError('Universe CSV must contain a ticker-like column.')
        values = [row.get(ticker_field) for row in reader]
    return _selection_from_values(universe_id=universe_id, source=f'csv:{path.name}', values=values)


def _selection_from_values(*, universe_id: str, source: str, values: Iterable[str | None]) -> UniverseTickerSelection:
    normalized_values: list[str] = []
    invalid_values: list[str] = []
    seen: set[str] = set()
    input_count = 0
    for value in values:
        input_count += 1
        normalized = normalize_ticker(value)
        if normalized is None:
            invalid_values.append('' if value is None else str(value))
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        normalized_values.append(normalized)
    return UniverseTickerSelection(
        universe_id=universe_id,
        source=source,
        input_count=input_count,
        tickers=tuple(normalized_values),
        invalid_values=tuple(invalid_values),
    )


def _selection_from_universe_cache(*, database: ReadOnlySQLite, universe_id: str) -> UniverseTickerSelection | None:
    tables = set(database.list_tables())
    if UNIVERSE_CACHE_TABLE not in tables:
        return None
    columns = {column.name.lower() for column in database.list_columns(UNIVERSE_CACHE_TABLE)}
    required_columns = {'universe_key', 'tickers_json'}
    if not required_columns.issubset(columns):
        return None
    row = database.fetch_one(
        """
        SELECT universe_key, tickers_json
        FROM "universe_cache"
        WHERE universe_key = ?
        """,
        (universe_id,),
    )
    if row is None:
        return None
    tickers_json = row['tickers_json']
    try:
        values = json.loads(str(tickers_json))
    except json.JSONDecodeError as exc:
        raise ValueError(f'Universe "{universe_id}" has invalid tickers_json in universe_cache.') from exc
    if not isinstance(values, list):
        raise ValueError(f'Universe "{universe_id}" tickers_json must decode to a list.')
    return _selection_from_values(
        universe_id=universe_id,
        source=f'universe_cache:{universe_id}',
        values=values,
    )

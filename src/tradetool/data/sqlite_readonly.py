from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import sqlite3


READ_ONLY_PREFIXES = ('SELECT', 'WITH', 'PRAGMA')
PRICE_TABLE_NAME_HINTS = ('price', 'prices', 'ohlc', 'quote', 'quotes', 'history', 'daily', 'eod')
UNIVERSE_TABLE_NAME_HINTS = ('universe', 'member', 'membership', 'ticker', 'symbol', 'constituent')
TICKER_COLUMN_HINTS = ('ticker', 'symbol', 'ric')
DATE_COLUMN_HINTS = ('date', 'trade_date', 'price_date', 'as_of_date')
PRICE_VALUE_HINTS = ('close', 'adj_close', 'adjusted_close', 'last')


@dataclass(frozen=True, slots=True)
class SchemaTableColumn:
    name: str
    declared_type: str
    not_null: bool
    default_value: str | None
    primary_key_position: int


@dataclass(frozen=True, slots=True)
class SchemaInspection:
    tables: tuple[str, ...]
    columns_by_table: Mapping[str, tuple[SchemaTableColumn, ...]]
    price_history_candidates: tuple[str, ...]
    universe_candidates: tuple[str, ...]
    warnings: tuple[str, ...]


class ReadOnlySQLite:
    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path).expanduser().resolve()
        if not self.path.exists():
            raise FileNotFoundError(f'SQLite database path does not exist: {self.path}')
        if not self.path.is_file():
            raise FileNotFoundError(f'SQLite database path is not a file: {self.path}')

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f'file:{self.path}?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def fetch_all(self, sql: str, parameters: Sequence[object] = ()) -> list[sqlite3.Row]:
        self._ensure_read_only_sql(sql)
        with self.connect() as connection:
            return list(connection.execute(sql, tuple(parameters)).fetchall())

    def fetch_one(self, sql: str, parameters: Sequence[object] = ()) -> sqlite3.Row | None:
        self._ensure_read_only_sql(sql)
        with self.connect() as connection:
            return connection.execute(sql, tuple(parameters)).fetchone()

    def list_tables(self) -> tuple[str, ...]:
        rows = self.fetch_all(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
        return tuple(str(row['name']) for row in rows)

    def list_columns(self, table_name: str) -> tuple[SchemaTableColumn, ...]:
        rows = self.fetch_all(f'PRAGMA table_info("{table_name}")')
        return tuple(
            SchemaTableColumn(
                name=str(row['name']),
                declared_type=str(row['type'] or ''),
                not_null=bool(row['notnull']),
                default_value=None if row['dflt_value'] is None else str(row['dflt_value']),
                primary_key_position=int(row['pk']),
            )
            for row in rows
        )

    def inspect_schema(self) -> SchemaInspection:
        return inspect_database_schema(self)

    @staticmethod
    def _ensure_read_only_sql(sql: str) -> None:
        normalized = sql.lstrip().upper()
        if not normalized.startswith(READ_ONLY_PREFIXES):
            raise ValueError('Only read-only SELECT/WITH/PRAGMA statements are allowed in Phase 3a.')


def inspect_database_schema(database: ReadOnlySQLite) -> SchemaInspection:
    tables = database.list_tables()
    columns_by_table = {table: database.list_columns(table) for table in tables}
    price_candidates = tuple(
        table for table, columns in columns_by_table.items() if _is_price_history_candidate(table, columns)
    )
    universe_candidates = tuple(
        table for table, columns in columns_by_table.items() if _is_universe_candidate(table, columns)
    )
    warnings: list[str] = []
    if not price_candidates:
        warnings.append('No likely price-history table was detected.')
    if not universe_candidates:
        warnings.append('No likely universe-membership table was detected.')
    return SchemaInspection(
        tables=tables,
        columns_by_table=columns_by_table,
        price_history_candidates=price_candidates,
        universe_candidates=universe_candidates,
        warnings=tuple(warnings),
    )


def _is_price_history_candidate(table_name: str, columns: Sequence[SchemaTableColumn]) -> bool:
    lowered_name = table_name.lower()
    column_names = {column.name.lower() for column in columns}
    if not any(hint in lowered_name for hint in PRICE_TABLE_NAME_HINTS):
        return False
    return (
        any(name in column_names for name in TICKER_COLUMN_HINTS)
        and any(name in column_names for name in DATE_COLUMN_HINTS)
        and any(name in column_names for name in PRICE_VALUE_HINTS)
    )


def _is_universe_candidate(table_name: str, columns: Sequence[SchemaTableColumn]) -> bool:
    lowered_name = table_name.lower()
    column_names = {column.name.lower() for column in columns}
    if not any(hint in lowered_name for hint in UNIVERSE_TABLE_NAME_HINTS):
        return False
    return any(name in column_names for name in TICKER_COLUMN_HINTS)

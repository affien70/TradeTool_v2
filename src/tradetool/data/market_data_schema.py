from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import sqlite3

LEGACY_PRODUCTION_DB_PATH = Path('/Users/affien/DEV/TradeTool/portfolio.sqlite').resolve()
REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_TMP_DIR = REPO_ROOT / 'tmp'
SYSTEM_TMP_DIR = Path('/tmp').resolve()
V2_PRICE_TABLE_NAME = 'price_history_v2'
V2_PRICE_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {V2_PRICE_TABLE_NAME} (
    ticker TEXT NOT NULL CHECK (TRIM(ticker) <> ''),
    price_date TEXT NOT NULL CHECK (TRIM(price_date) <> ''),
    raw_open REAL CHECK (raw_open IS NULL OR raw_open > 0),
    raw_high REAL CHECK (raw_high IS NULL OR raw_high > 0),
    raw_low REAL CHECK (raw_low IS NULL OR raw_low > 0),
    raw_close REAL CHECK (raw_close IS NULL OR raw_close > 0),
    adjusted_close REAL NOT NULL CHECK (adjusted_close > 0),
    volume REAL NOT NULL CHECK (volume >= 0),
    data_source TEXT NOT NULL CHECK (TRIM(data_source) <> ''),
    created_at_utc TEXT NOT NULL CHECK (TRIM(created_at_utc) <> ''),
    updated_at_utc TEXT NOT NULL CHECK (TRIM(updated_at_utc) <> ''),
    CHECK (raw_high IS NULL OR raw_low IS NULL OR raw_high >= raw_low),
    CHECK (raw_high IS NULL OR raw_open IS NULL OR raw_high >= raw_open),
    CHECK (raw_high IS NULL OR raw_close IS NULL OR raw_high >= raw_close),
    CHECK (raw_low IS NULL OR raw_open IS NULL OR raw_low <= raw_open),
    CHECK (raw_low IS NULL OR raw_close IS NULL OR raw_low <= raw_close),
    PRIMARY KEY (ticker, price_date, data_source)
)
"""


@dataclass(frozen=True, slots=True)
class MarketDataSchemaColumn:
    name: str
    declared_type: str
    not_null: bool
    default_value: str | None
    primary_key_position: int

    def to_dict(self) -> dict[str, object]:
        return {
            'name': self.name,
            'declared_type': self.declared_type,
            'not_null': self.not_null,
            'default_value': self.default_value,
            'primary_key_position': self.primary_key_position,
        }


@dataclass(frozen=True, slots=True)
class MarketDataRow:
    ticker: str
    price_date: str
    raw_open: float | None
    raw_high: float | None
    raw_low: float | None
    raw_close: float | None
    adjusted_close: float
    volume: float
    data_source: str
    created_at_utc: str
    updated_at_utc: str

    def key(self) -> tuple[str, str, str]:
        return (self.ticker.strip().upper(), self.price_date.strip(), self.data_source.strip())


@dataclass(frozen=True, slots=True)
class MarketDataDryRunValidationRow:
    ticker: str
    price_date: str
    data_source: str
    valid: bool
    reasons: tuple[str, ...]
    action: str

    def to_dict(self) -> dict[str, object]:
        return {
            'ticker': self.ticker,
            'price_date': self.price_date,
            'data_source': self.data_source,
            'valid': self.valid,
            'reasons': '; '.join(self.reasons),
            'action': self.action,
        }


@dataclass(frozen=True, slots=True)
class MarketDataDryRunResult:
    valid_row_count: int
    invalid_row_count: int
    invalid_reasons: Mapping[str, int]
    would_insert: int
    would_update: int
    would_skip: int
    rows: tuple[MarketDataDryRunValidationRow, ...]


@dataclass(frozen=True, slots=True)
class MarketDataSchemaInspectionResult:
    db_path: Path
    table_name: str
    schema_initialized: bool
    row_count: int
    columns: tuple[MarketDataSchemaColumn, ...]
    indexes: tuple[str, ...]

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'db_path': str(self.db_path),
            'table_name': self.table_name,
            'schema_initialized': self.schema_initialized,
            'row_count': self.row_count,
            'column_count': len(self.columns),
            'columns': [column.to_dict() for column in self.columns],
            'indexes': list(self.indexes),
        }


def validate_market_data_db_path(db_path: str | Path) -> Path:
    resolved = Path(db_path).expanduser().resolve()
    if resolved == LEGACY_PRODUCTION_DB_PATH:
        raise ValueError(f'Legacy production DB path is refused for v2 schema work: {resolved}')
    if resolved.is_dir():
        raise ValueError(f'Market-data schema path must be a SQLite file path, not a directory: {resolved}')
    allowed_tmp = resolved.is_relative_to(SYSTEM_TMP_DIR)
    allowed_repo_tmp = resolved.is_relative_to(REPO_TMP_DIR)
    if not (allowed_tmp or allowed_repo_tmp):
        raise ValueError(f'Market-data schema path must be under /tmp or repo-local tmp/: {resolved}')
    return resolved


def initialize_market_data_schema(db_path: str | Path) -> MarketDataSchemaInspectionResult:
    resolved = validate_market_data_db_path(db_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(resolved) as connection:
        connection.execute(V2_PRICE_TABLE_DDL)
        connection.commit()
    return inspect_market_data_schema(resolved)


def inspect_market_data_schema(db_path: str | Path) -> MarketDataSchemaInspectionResult:
    resolved = validate_market_data_db_path(db_path)
    if not resolved.exists():
        raise FileNotFoundError(f'Market-data schema DB path does not exist: {resolved}')
    with sqlite3.connect(resolved) as connection:
        connection.row_factory = sqlite3.Row
        table_row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (V2_PRICE_TABLE_NAME,),
        ).fetchone()
        schema_initialized = table_row is not None
        columns = ()
        row_count = 0
        indexes: tuple[str, ...] = ()
        if schema_initialized:
            pragma_rows = connection.execute(f'PRAGMA table_info("{V2_PRICE_TABLE_NAME}")').fetchall()
            columns = tuple(
                MarketDataSchemaColumn(
                    name=str(row['name']),
                    declared_type=str(row['type'] or ''),
                    not_null=bool(row['notnull']),
                    default_value=None if row['dflt_value'] is None else str(row['dflt_value']),
                    primary_key_position=int(row['pk']),
                )
                for row in pragma_rows
            )
            row_count = int(connection.execute(f'SELECT COUNT(*) FROM "{V2_PRICE_TABLE_NAME}"').fetchone()[0])
            indexes = tuple(
                str(row['name'])
                for row in connection.execute(f'PRAGMA index_list("{V2_PRICE_TABLE_NAME}")').fetchall()
            )
    return MarketDataSchemaInspectionResult(
        db_path=resolved,
        table_name=V2_PRICE_TABLE_NAME,
        schema_initialized=schema_initialized,
        row_count=row_count,
        columns=columns,
        indexes=indexes,
    )


def dry_run_market_data_rows(
    *,
    db_path: str | Path,
    rows: Sequence[MarketDataRow],
) -> MarketDataDryRunResult:
    resolved = validate_market_data_db_path(db_path)
    existing_rows = _load_existing_keys(resolved) if resolved.exists() else {}
    results: list[MarketDataDryRunValidationRow] = []
    invalid_reasons: Counter[str] = Counter()
    would_insert = 0
    would_update = 0
    would_skip = 0

    for row in rows:
        reasons = _validate_market_data_row(row)
        if reasons:
            invalid_reasons.update(reasons)
            results.append(
                MarketDataDryRunValidationRow(
                    ticker=row.ticker,
                    price_date=row.price_date,
                    data_source=row.data_source,
                    valid=False,
                    reasons=reasons,
                    action='invalid',
                )
            )
            continue
        existing = existing_rows.get(row.key())
        action = 'would_insert'
        if existing is not None:
            if existing == _row_payload_signature(row):
                action = 'would_skip'
            else:
                action = 'would_update'
        if action == 'would_insert':
            would_insert += 1
        elif action == 'would_update':
            would_update += 1
        else:
            would_skip += 1
        results.append(
            MarketDataDryRunValidationRow(
                ticker=row.ticker,
                price_date=row.price_date,
                data_source=row.data_source,
                valid=True,
                reasons=(),
                action=action,
            )
        )
    return MarketDataDryRunResult(
        valid_row_count=sum(1 for row in results if row.valid),
        invalid_row_count=sum(1 for row in results if not row.valid),
        invalid_reasons=dict(sorted(invalid_reasons.items())),
        would_insert=would_insert,
        would_update=would_update,
        would_skip=would_skip,
        rows=tuple(results),
    )


def _apply_market_data_rows_for_test(*, db_path: str | Path, rows: Sequence[MarketDataRow]) -> None:
    resolved = validate_market_data_db_path(db_path)
    if not resolved.exists():
        raise FileNotFoundError(f'Market-data schema DB path does not exist: {resolved}')
    with sqlite3.connect(resolved) as connection:
        connection.execute(V2_PRICE_TABLE_DDL)
        for row in rows:
            reasons = _validate_market_data_row(row)
            if reasons:
                raise ValueError(f'Invalid market-data row for test write: {", ".join(reasons)}')
            connection.execute(
                f"""
                INSERT INTO {V2_PRICE_TABLE_NAME} (
                    ticker, price_date, raw_open, raw_high, raw_low, raw_close,
                    adjusted_close, volume, data_source, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.ticker.strip().upper(),
                    row.price_date.strip(),
                    row.raw_open,
                    row.raw_high,
                    row.raw_low,
                    row.raw_close,
                    row.adjusted_close,
                    row.volume,
                    row.data_source.strip(),
                    row.created_at_utc.strip(),
                    row.updated_at_utc.strip(),
                ),
            )
        connection.commit()


def _validate_market_data_row(row: MarketDataRow) -> tuple[str, ...]:
    reasons: list[str] = []
    ticker = row.ticker.strip()
    price_date = row.price_date.strip()
    data_source = row.data_source.strip()
    if not ticker:
        reasons.append('empty_ticker')
    if not price_date:
        reasons.append('empty_price_date')
    if not data_source:
        reasons.append('empty_data_source')
    if row.adjusted_close <= 0:
        reasons.append('nonpositive_adjusted_close')
    if row.volume < 0:
        reasons.append('negative_volume')

    raw_values = {
        'raw_open': row.raw_open,
        'raw_high': row.raw_high,
        'raw_low': row.raw_low,
        'raw_close': row.raw_close,
    }
    for label, value in raw_values.items():
        if value is not None and value <= 0:
            reasons.append(f'nonpositive_{label}')
    if row.raw_high is not None and row.raw_low is not None and row.raw_high < row.raw_low:
        reasons.append('raw_high_lower_than_raw_low')
    if row.raw_high is not None and row.raw_open is not None and row.raw_high < row.raw_open:
        reasons.append('raw_high_lower_than_raw_open')
    if row.raw_high is not None and row.raw_close is not None and row.raw_high < row.raw_close:
        reasons.append('raw_high_lower_than_raw_close')
    if row.raw_low is not None and row.raw_open is not None and row.raw_low > row.raw_open:
        reasons.append('raw_low_higher_than_raw_open')
    if row.raw_low is not None and row.raw_close is not None and row.raw_low > row.raw_close:
        reasons.append('raw_low_higher_than_raw_close')
    return tuple(sorted(set(reasons)))


def _load_existing_keys(db_path: Path) -> dict[tuple[str, str, str], tuple[object, ...]]:
    if not db_path.exists():
        return {}
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        table_row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (V2_PRICE_TABLE_NAME,),
        ).fetchone()
        if table_row is None:
            return {}
        rows = connection.execute(
            f"""
            SELECT ticker, price_date, data_source, raw_open, raw_high, raw_low, raw_close,
                   adjusted_close, volume, created_at_utc, updated_at_utc
            FROM {V2_PRICE_TABLE_NAME}
            """
        ).fetchall()
    return {
        (str(row['ticker']).strip().upper(), str(row['price_date']).strip(), str(row['data_source']).strip()): (
            row['raw_open'],
            row['raw_high'],
            row['raw_low'],
            row['raw_close'],
            row['adjusted_close'],
            row['volume'],
            row['created_at_utc'],
            row['updated_at_utc'],
        )
        for row in rows
    }


def _row_payload_signature(row: MarketDataRow) -> tuple[object, ...]:
    return (
        row.raw_open,
        row.raw_high,
        row.raw_low,
        row.raw_close,
        row.adjusted_close,
        row.volume,
        row.created_at_utc.strip(),
        row.updated_at_utc.strip(),
    )

"""Explicit, read-only V1 Holdings compatibility import for approved V2 storage."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
import sqlite3

from tradetool.holdings.core import HoldingTransaction, fallback_transaction_key
from tradetool.holdings.storage import (
    HOLDINGS_SETTINGS_TABLE,
    HOLDINGS_TRANSACTIONS_TABLE,
    HoldingTransactionRecord,
    upsert_holding_transaction,
)


V1_HOLDINGS_TABLE = 'holdings'
_V1_SOURCE_COLUMNS = (
    'id',
    'ticker',
    'ticker_source',
    'fullname',
    'isin',
    'Verdipapir',
    'buy_date',
    'settlement_date',
    'shares',
    'buy_price',
    'note',
    'transaksjonstype',
    'valuta',
    'belop',
    'kurtasje',
    'nordnet_resultat',
    'transaksjons_id',
    'transaction_key',
    'cost_basis_missing',
)


@dataclass(frozen=True, slots=True)
class V1HoldingsImportResult:
    source_rows: int
    mapped_rows: int
    invalid_rows: int
    duplicate_broker_identity_rows: int
    duplicate_fallback_identity_rows: int
    would_insert: int
    would_update: int
    would_skip: int
    written_rows: int = 0


def dry_run_v1_holdings_import(
    v1_database_path: str | Path,
    *,
    target_connection: sqlite3.Connection | None = None,
) -> V1HoldingsImportResult:
    records, source_rows, invalid_rows, duplicate_broker_rows, duplicate_fallback_rows = _read_v1_records(v1_database_path)
    existing_broker_ids, existing_fallback_keys = _target_identities(target_connection)
    would_insert = 0
    would_update = 0
    for record in records:
        broker_id = _normalized_text(record.transaction.nordnet_transaction_id)
        if record.fallback_key in existing_fallback_keys or (broker_id and broker_id in existing_broker_ids):
            would_update += 1
        else:
            would_insert += 1
    return V1HoldingsImportResult(
        source_rows=source_rows,
        mapped_rows=len(records),
        invalid_rows=invalid_rows,
        duplicate_broker_identity_rows=duplicate_broker_rows,
        duplicate_fallback_identity_rows=duplicate_fallback_rows,
        would_insert=would_insert,
        would_update=would_update,
        would_skip=0,
    )


def import_v1_holdings(
    v1_database_path: str | Path,
    *,
    target_connection: sqlite3.Connection,
) -> V1HoldingsImportResult:
    _require_initialized_target_schema(target_connection)
    records, source_rows, invalid_rows, duplicate_broker_rows, duplicate_fallback_rows = _read_v1_records(v1_database_path)
    existing_broker_ids, existing_fallback_keys = _target_identities(target_connection)
    would_insert = 0
    would_update = 0
    for record in records:
        broker_id = _normalized_text(record.transaction.nordnet_transaction_id)
        if record.fallback_key in existing_fallback_keys or (broker_id and broker_id in existing_broker_ids):
            would_update += 1
        else:
            would_insert += 1
        upsert_holding_transaction(target_connection, record)
        existing_fallback_keys.add(record.fallback_key)
        if broker_id:
            existing_broker_ids.add(broker_id)
    return V1HoldingsImportResult(
        source_rows=source_rows,
        mapped_rows=len(records),
        invalid_rows=invalid_rows,
        duplicate_broker_identity_rows=duplicate_broker_rows,
        duplicate_fallback_identity_rows=duplicate_fallback_rows,
        would_insert=would_insert,
        would_update=would_update,
        would_skip=0,
        written_rows=len(records),
    )


def _read_v1_records(
    v1_database_path: str | Path,
) -> tuple[list[HoldingTransactionRecord], int, int, int, int]:
    v1_path = _validate_v1_database_path(v1_database_path)
    with sqlite3.connect(f'file:{v1_path}?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        _require_v1_holdings_schema(connection)
        rows = connection.execute(
            f"SELECT {', '.join(_quote_identifier(column) for column in _V1_SOURCE_COLUMNS)} "
            f'FROM {_quote_identifier(V1_HOLDINGS_TABLE)} ORDER BY id ASC'
        ).fetchall()

    records: list[HoldingTransactionRecord] = []
    invalid_rows = 0
    duplicate_broker_rows = 0
    duplicate_fallback_rows = 0
    seen_broker_ids: set[str] = set()
    seen_fallback_keys: set[str] = set()
    for row in rows:
        try:
            record = _map_v1_row(row)
        except (TypeError, ValueError):
            invalid_rows += 1
            continue
        broker_id = _normalized_text(record.transaction.nordnet_transaction_id)
        if broker_id and broker_id in seen_broker_ids:
            duplicate_broker_rows += 1
        if record.fallback_key in seen_fallback_keys:
            duplicate_fallback_rows += 1
        if broker_id:
            seen_broker_ids.add(broker_id)
        seen_fallback_keys.add(record.fallback_key)
        records.append(record)
    return records, len(rows), invalid_rows, duplicate_broker_rows, duplicate_fallback_rows


def _map_v1_row(row: sqlite3.Row) -> HoldingTransactionRecord:
    transaction = HoldingTransaction(
        transaction_type=_required_text(row['transaksjonstype'], field_name='transaksjonstype'),
        shares=_required_finite_float(row['shares'], field_name='shares'),
        price=_optional_finite_float(row['buy_price'], field_name='buy_price'),
        amount=_optional_finite_float(row['belop'], field_name='belop'),
        trade_date=_required_text(row['buy_date'], field_name='buy_date'),
        settlement_date=_optional_text(row['settlement_date']),
        isin=_optional_text(row['isin']),
        ticker=_optional_text(row['ticker']),
        instrument_name=_optional_text(row['Verdipapir']) or _optional_text(row['fullname']),
        currency=_optional_text(row['valuta']),
        nordnet_transaction_id=_optional_text(row['transaksjons_id']),
    )
    record = HoldingTransactionRecord.from_transaction(
        transaction,
        ticker_source=_optional_text(row['ticker_source']),
        note=_optional_text(row['note']),
        fee=_optional_finite_float(row['kurtasje'], field_name='kurtasje'),
        nordnet_result=_optional_finite_float(row['nordnet_resultat'], field_name='nordnet_resultat'),
        source_cost_basis_missing=_boolean_value(row['cost_basis_missing'], field_name='cost_basis_missing'),
    )
    source_fallback_key = _optional_text(row['transaction_key'])
    if source_fallback_key is not None and source_fallback_key != fallback_transaction_key(transaction):
        raise ValueError('V1 transaction_key does not match the H1 deterministic fallback key.')
    return record


def _target_identities(connection: sqlite3.Connection | None) -> tuple[set[str], set[str]]:
    if connection is None:
        return set(), set()
    _require_initialized_target_schema(connection)
    rows = connection.execute(
        f'SELECT nordnet_transaction_id, fallback_transaction_key FROM {_quote_identifier(HOLDINGS_TRANSACTIONS_TABLE)}'
    ).fetchall()
    broker_ids = {_normalized_text(row[0]) for row in rows if _normalized_text(row[0])}
    fallback_keys = {str(row[1]) for row in rows}
    return broker_ids, fallback_keys


def _require_initialized_target_schema(connection: sqlite3.Connection) -> None:
    table_names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)",
            (HOLDINGS_TRANSACTIONS_TABLE, HOLDINGS_SETTINGS_TABLE),
        ).fetchall()
    }
    required_tables = {HOLDINGS_TRANSACTIONS_TABLE, HOLDINGS_SETTINGS_TABLE}
    if table_names != required_tables:
        raise ValueError('V2 Holdings schema must be initialized explicitly before import.')


def _require_v1_holdings_schema(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info({_quote_identifier(V1_HOLDINGS_TABLE)})').fetchall()
    }
    missing_columns = set(_V1_SOURCE_COLUMNS) - columns
    if missing_columns:
        missing_text = ', '.join(sorted(missing_columns))
        raise ValueError(f'V1 holdings schema is missing required source columns: {missing_text}')


def _validate_v1_database_path(v1_database_path: str | Path) -> Path:
    resolved = Path(v1_database_path).expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise FileNotFoundError(f'V1 Holdings database does not exist: {resolved}')
    return resolved


def _quote_identifier(identifier: str) -> str:
    return f'"{identifier.replace("\"", "\"\"")}"'


def _required_text(value: object, *, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f'{field_name} is required.')
    return text


def _optional_text(value: object) -> str | None:
    text = str(value or '').strip()
    return text or None


def _normalized_text(value: object) -> str:
    return str(value or '').strip()


def _required_finite_float(value: object, *, field_name: str) -> float:
    result = _optional_finite_float(value, field_name=field_name)
    if result is None:
        raise ValueError(f'{field_name} is required.')
    return result


def _optional_finite_float(value: object, *, field_name: str) -> float | None:
    if value is None or str(value).strip() == '':
        return None
    result = float(value)
    if not isfinite(result):
        raise ValueError(f'{field_name} must be finite when provided.')
    return result


def _boolean_value(value: object, *, field_name: str) -> bool:
    if value in (0, 1, False, True):
        return bool(value)
    raise ValueError(f'{field_name} must be 0 or 1.')
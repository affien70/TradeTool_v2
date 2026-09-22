"""Explicit SQLite persistence primitives for temporary and test Holdings databases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3

from tradetool.holdings.core import HoldingTransaction, fallback_transaction_key


HOLDINGS_TRANSACTIONS_TABLE = 'holdings_transactions_v2'
HOLDINGS_SETTINGS_TABLE = 'holdings_settings_v2'
DEFAULT_NORWAY_BENCHMARK_ID = 'OSEBX.OL'

HOLDINGS_TRANSACTIONS_DDL = f"""
CREATE TABLE IF NOT EXISTS {HOLDINGS_TRANSACTIONS_TABLE} (
    transaction_id INTEGER PRIMARY KEY,
    nordnet_transaction_id TEXT,
    fallback_transaction_key TEXT NOT NULL UNIQUE,
    trade_date TEXT NOT NULL,
    settlement_date TEXT,
    isin TEXT,
    ticker TEXT,
    instrument_name TEXT,
    ticker_source TEXT,
    note TEXT,
    transaction_type TEXT NOT NULL,
    shares REAL NOT NULL,
    price REAL,
    amount REAL,
    fee REAL,
    currency TEXT,
    nordnet_result REAL,
    source_cost_basis_missing INTEGER NOT NULL DEFAULT 0 CHECK (source_cost_basis_missing IN (0, 1)),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
)
"""

HOLDINGS_NORDNET_ID_INDEX_DDL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS ux_{HOLDINGS_TRANSACTIONS_TABLE}_nordnet_transaction_id
ON {HOLDINGS_TRANSACTIONS_TABLE} (nordnet_transaction_id)
WHERE nordnet_transaction_id IS NOT NULL AND TRIM(nordnet_transaction_id) <> ''
"""

HOLDINGS_ORDER_INDEX_DDL = f"""
CREATE INDEX IF NOT EXISTS ix_{HOLDINGS_TRANSACTIONS_TABLE}_position_order
ON {HOLDINGS_TRANSACTIONS_TABLE} (isin, ticker, trade_date, settlement_date, fallback_transaction_key)
"""

HOLDINGS_SETTINGS_DDL = f"""
CREATE TABLE IF NOT EXISTS {HOLDINGS_SETTINGS_TABLE} (
    settings_scope TEXT PRIMARY KEY CHECK (settings_scope = 'global'),
    period_label TEXT NOT NULL CHECK (period_label IN ('1 år', '2 år', '5 år')),
    rs_months INTEGER NOT NULL CHECK (rs_months IN (3, 6, 12)),
    sell_rs_weak INTEGER NOT NULL CHECK (sell_rs_weak IN (0, 1)),
    sell_below_cost_basis INTEGER NOT NULL CHECK (sell_below_cost_basis IN (0, 1)),
    sell_drop_from_peak INTEGER NOT NULL CHECK (sell_drop_from_peak IN (0, 1)),
    sell_fast_sma_days INTEGER NOT NULL CHECK (sell_fast_sma_days >= 0),
    atr_multiplier REAL NOT NULL CHECK (atr_multiplier > 0),
    rs_threshold REAL NOT NULL CHECK (rs_threshold > 0),
    norway_benchmark_id TEXT NOT NULL CHECK (TRIM(norway_benchmark_id) <> ''),
    updated_at_utc TEXT NOT NULL
)
"""


@dataclass(frozen=True, slots=True)
class HoldingTransactionRecord:
    transaction: HoldingTransaction
    fallback_key: str
    ticker_source: str | None
    note: str | None
    fee: float | None
    nordnet_result: float | None
    source_cost_basis_missing: bool
    created_at_utc: str
    updated_at_utc: str

    @classmethod
    def from_transaction(
        cls,
        transaction: HoldingTransaction,
        *,
        ticker_source: str | None = None,
        note: str | None = None,
        fee: float | None = None,
        nordnet_result: float | None = None,
        source_cost_basis_missing: bool = False,
        created_at_utc: str | None = None,
        updated_at_utc: str | None = None,
    ) -> HoldingTransactionRecord:
        timestamp = _utc_timestamp()
        return cls(
            transaction=transaction,
            fallback_key=fallback_transaction_key(transaction),
            ticker_source=ticker_source,
            note=note,
            fee=fee,
            nordnet_result=nordnet_result,
            source_cost_basis_missing=source_cost_basis_missing,
            created_at_utc=created_at_utc or timestamp,
            updated_at_utc=updated_at_utc or timestamp,
        )

    def __post_init__(self) -> None:
        if not _date_text(self.transaction.trade_date):
            raise ValueError('trade_date is required for Holdings persistence.')
        if self.fallback_key != fallback_transaction_key(self.transaction):
            raise ValueError('fallback_key must match the deterministic H2a transaction key.')
        if not self.created_at_utc.strip() or not self.updated_at_utc.strip():
            raise ValueError('created_at_utc and updated_at_utc must be non-empty.')


@dataclass(frozen=True, slots=True)
class HoldingSettings:
    period_label: str = '1 år'
    rs_months: int = 6
    sell_rs_weak: bool = True
    sell_below_cost_basis: bool = True
    sell_drop_from_peak: bool = False
    sell_fast_sma_days: int = 100
    atr_multiplier: float = 2.5
    rs_threshold: float = 1.05
    norway_benchmark_id: str = DEFAULT_NORWAY_BENCHMARK_ID

    @property
    def period_key(self) -> str:
        return {'1 år': '1y', '2 år': '2y', '5 år': '5y'}[self.period_label]


def initialize_holdings_schema(connection: sqlite3.Connection) -> None:
    """Create Holdings tables only on an explicitly supplied writable connection."""
    connection.execute(HOLDINGS_TRANSACTIONS_DDL)
    connection.execute(HOLDINGS_NORDNET_ID_INDEX_DDL)
    connection.execute(HOLDINGS_ORDER_INDEX_DDL)
    connection.execute(HOLDINGS_SETTINGS_DDL)
    connection.commit()


def upsert_holding_transaction(
    connection: sqlite3.Connection,
    record: HoldingTransactionRecord,
) -> HoldingTransactionRecord:
    _require_table(connection, HOLDINGS_TRANSACTIONS_TABLE)
    transaction = record.transaction
    connection.execute(
        f"""
        INSERT INTO {HOLDINGS_TRANSACTIONS_TABLE} (
            nordnet_transaction_id, fallback_transaction_key, trade_date, settlement_date,
            isin, ticker, instrument_name, ticker_source, note, transaction_type, shares,
            price, amount, fee, currency, nordnet_result, source_cost_basis_missing,
            created_at_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO UPDATE SET
            nordnet_transaction_id = excluded.nordnet_transaction_id,
            fallback_transaction_key = excluded.fallback_transaction_key,
            trade_date = excluded.trade_date,
            settlement_date = excluded.settlement_date,
            isin = excluded.isin,
            ticker = excluded.ticker,
            instrument_name = excluded.instrument_name,
            ticker_source = excluded.ticker_source,
            note = excluded.note,
            transaction_type = excluded.transaction_type,
            shares = excluded.shares,
            price = excluded.price,
            amount = excluded.amount,
            fee = excluded.fee,
            currency = excluded.currency,
            nordnet_result = excluded.nordnet_result,
            source_cost_basis_missing = excluded.source_cost_basis_missing,
            updated_at_utc = excluded.updated_at_utc
        """,
        _transaction_parameters(record),
    )
    connection.commit()
    return _read_record_by_identity(connection, record)


def load_holding_transactions(connection: sqlite3.Connection) -> tuple[HoldingTransactionRecord, ...]:
    _require_table(connection, HOLDINGS_TRANSACTIONS_TABLE)
    rows = connection.execute(
        f"""
        SELECT *
        FROM {HOLDINGS_TRANSACTIONS_TABLE}
        ORDER BY trade_date, settlement_date, fallback_transaction_key, transaction_id
        """
    ).fetchall()
    return tuple(_record_from_row(row) for row in rows)


def load_holding_settings(connection: sqlite3.Connection) -> HoldingSettings:
    if not _table_exists(connection, HOLDINGS_SETTINGS_TABLE):
        return HoldingSettings()
    row = connection.execute(
        f"SELECT * FROM {HOLDINGS_SETTINGS_TABLE} WHERE settings_scope = 'global'"
    ).fetchone()
    if row is None:
        return HoldingSettings()
    return HoldingSettings(
        period_label=str(row['period_label']),
        rs_months=int(row['rs_months']),
        sell_rs_weak=bool(row['sell_rs_weak']),
        sell_below_cost_basis=bool(row['sell_below_cost_basis']),
        sell_drop_from_peak=bool(row['sell_drop_from_peak']),
        sell_fast_sma_days=int(row['sell_fast_sma_days']),
        atr_multiplier=float(row['atr_multiplier']),
        rs_threshold=float(row['rs_threshold']),
        norway_benchmark_id=str(row['norway_benchmark_id']),
    )


def save_holding_settings(connection: sqlite3.Connection, settings: HoldingSettings) -> HoldingSettings:
    _require_table(connection, HOLDINGS_SETTINGS_TABLE)
    connection.execute(
        f"""
        INSERT INTO {HOLDINGS_SETTINGS_TABLE} (
            settings_scope, period_label, rs_months, sell_rs_weak,
            sell_below_cost_basis, sell_drop_from_peak, sell_fast_sma_days,
            atr_multiplier, rs_threshold, norway_benchmark_id, updated_at_utc
        ) VALUES ('global', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(settings_scope) DO UPDATE SET
            period_label = excluded.period_label,
            rs_months = excluded.rs_months,
            sell_rs_weak = excluded.sell_rs_weak,
            sell_below_cost_basis = excluded.sell_below_cost_basis,
            sell_drop_from_peak = excluded.sell_drop_from_peak,
            sell_fast_sma_days = excluded.sell_fast_sma_days,
            atr_multiplier = excluded.atr_multiplier,
            rs_threshold = excluded.rs_threshold,
            norway_benchmark_id = excluded.norway_benchmark_id,
            updated_at_utc = excluded.updated_at_utc
        """,
        (
            settings.period_label,
            settings.rs_months,
            int(settings.sell_rs_weak),
            int(settings.sell_below_cost_basis),
            int(settings.sell_drop_from_peak),
            settings.sell_fast_sma_days,
            settings.atr_multiplier,
            settings.rs_threshold,
            settings.norway_benchmark_id,
            _utc_timestamp(),
        ),
    )
    connection.commit()
    return load_holding_settings(connection)


def _transaction_parameters(record: HoldingTransactionRecord) -> tuple[object, ...]:
    transaction = record.transaction
    return (
        _normalized_optional_text(transaction.nordnet_transaction_id),
        record.fallback_key,
        _date_text(transaction.trade_date),
        _date_text(transaction.settlement_date) or None,
        _normalized_optional_text(transaction.isin),
        _normalized_optional_text(transaction.ticker),
        _normalized_optional_text(transaction.instrument_name),
        _normalized_optional_text(record.ticker_source),
        record.note,
        transaction.transaction_type,
        transaction.shares,
        transaction.price,
        transaction.amount,
        record.fee,
        _normalized_optional_text(transaction.currency),
        record.nordnet_result,
        int(record.source_cost_basis_missing),
        record.created_at_utc,
        record.updated_at_utc,
    )


def _read_record_by_identity(connection: sqlite3.Connection, record: HoldingTransactionRecord) -> HoldingTransactionRecord:
    nordnet_transaction_id = _normalized_optional_text(record.transaction.nordnet_transaction_id)
    row = connection.execute(
        f"""
        SELECT *
        FROM {HOLDINGS_TRANSACTIONS_TABLE}
        WHERE fallback_transaction_key = ?
           OR (? IS NOT NULL AND nordnet_transaction_id = ?)
        ORDER BY transaction_id
        LIMIT 1
        """,
        (record.fallback_key, nordnet_transaction_id, nordnet_transaction_id),
    ).fetchone()
    if row is None:
        raise RuntimeError('Persisted Holdings transaction could not be read back.')
    return _record_from_row(row)


def _record_from_row(row: sqlite3.Row | tuple[object, ...]) -> HoldingTransactionRecord:
    return HoldingTransactionRecord(
        transaction=HoldingTransaction(
            transaction_type=str(row['transaction_type']),
            shares=float(row['shares']),
            price=_optional_float(row['price']),
            amount=_optional_float(row['amount']),
            trade_date=str(row['trade_date']),
            settlement_date=_optional_text(row['settlement_date']),
            isin=_optional_text(row['isin']),
            ticker=_optional_text(row['ticker']),
            instrument_name=_optional_text(row['instrument_name']),
            currency=_optional_text(row['currency']),
            nordnet_transaction_id=_optional_text(row['nordnet_transaction_id']),
        ),
        fallback_key=str(row['fallback_transaction_key']),
        ticker_source=_optional_text(row['ticker_source']),
        note=_optional_text(row['note']),
        fee=_optional_float(row['fee']),
        nordnet_result=_optional_float(row['nordnet_result']),
        source_cost_basis_missing=bool(row['source_cost_basis_missing']),
        created_at_utc=str(row['created_at_utc']),
        updated_at_utc=str(row['updated_at_utc']),
    )


def _require_table(connection: sqlite3.Connection, table_name: str) -> None:
    if not _table_exists(connection, table_name):
        raise ValueError(f'Holdings schema is not initialized: {table_name}')


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _date_text(value: object) -> str:
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.date().isoformat()
    if hasattr(value, 'isoformat'):
        return str(value.isoformat())
    return str(value).strip()


def _normalized_optional_text(value: object) -> str | None:
    normalized = str(value or '').strip()
    return normalized or None


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
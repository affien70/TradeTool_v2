"""V2-native Nordnet raw-file parsing and explicit Holdings import services."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from math import isfinite
import sqlite3
from typing import BinaryIO, Literal

from tradetool.holdings.core import HoldingTransaction
from tradetool.holdings.storage import (
    HOLDINGS_SETTINGS_TABLE,
    HOLDINGS_TRANSACTIONS_TABLE,
    HoldingTransactionRecord,
    load_holding_transactions,
    upsert_holding_transaction,
)


NordnetTransactionClassification = Literal[
    'POSITION_ADDITION',
    'POSITION_REDUCTION',
    'HOLDINGS_CASHFLOW',
    'NON_HOLDINGS_ACCOUNT_CASHFLOW',
    'ADMINISTRATIVE_IGNORE',
    'UNSUPPORTED_BLOCKER',
]

REQUIRED_NORDNET_COLUMNS = (
    'Id',
    'Handelsdag',
    'Transaksjonstype',
    'Verdipapir',
    'ISIN',
    'Antall',
    'Kurs',
    'Valuta',
    'Beløp',
    'Transaksjonstekst',
)
SETTLEMENT_DATE_COLUMNS = ('Oppgjørsdag', 'Oppgjørsdato', 'Settlement date', 'Settlement Date')

_PRIMARY_CURRENCY_OCCURRENCE = 0
_MAPPED_TRANSACTION_TYPES: dict[str, tuple[str, NordnetTransactionClassification]] = {
    'KJØPT': ('KJØPT', 'POSITION_ADDITION'),
    'INNLEGG OVERFØRING': ('INNLEGG OVERFØRING', 'POSITION_ADDITION'),
    'INNLEGG': ('INNLEGG', 'POSITION_ADDITION'),
    'SALG': ('SALG', 'POSITION_REDUCTION'),
    'INNLØSN. UTTAK VP': ('SALG', 'POSITION_REDUCTION'),
    'UTTAK VP': ('SALG', 'POSITION_REDUCTION'),
    'INNLØSNING': ('SALG', 'POSITION_REDUCTION'),
    'UTBYTTE': ('UTBYTTE', 'HOLDINGS_CASHFLOW'),
    'PLATTFORMAVGIFT': ('PLATTFORMAVGIFT', 'HOLDINGS_CASHFLOW'),
    'TILBAKEBET. FOND AVG': ('PLATTFORMAVGIFT', 'HOLDINGS_CASHFLOW'),
    'PLATTFORMAVG KORR': ('PLATTFORMAVGIFT', 'HOLDINGS_CASHFLOW'),
}
_IGNORED_TRANSACTION_TYPES: dict[str, NordnetTransactionClassification] = {
    'INNSKUDD': 'NON_HOLDINGS_ACCOUNT_CASHFLOW',
    'UTTAK INTERNT': 'NON_HOLDINGS_ACCOUNT_CASHFLOW',
    'OVERFØRING VIA TRUSTLY': 'NON_HOLDINGS_ACCOUNT_CASHFLOW',
    'TILDELING INNLEGG RE': 'ADMINISTRATIVE_IGNORE',
    'SLETTING UTTAK VP': 'ADMINISTRATIVE_IGNORE',
    'INNL. VP LIKVID': 'ADMINISTRATIVE_IGNORE',
    'BYTTE INNLEGG VP': 'ADMINISTRATIVE_IGNORE',
    'BYTTE UTTAK VP': 'ADMINISTRATIVE_IGNORE',
}


@dataclass(frozen=True, slots=True)
class NordnetImportIssue:
    code: str
    message: str
    source_row_number: int | None = None
    severity: Literal['error', 'warning'] = 'error'


@dataclass(frozen=True, slots=True)
class NordnetMappedTransaction:
    source_row_number: int
    record: HoldingTransactionRecord
    classification: Literal['POSITION_ADDITION', 'POSITION_REDUCTION', 'HOLDINGS_CASHFLOW']


@dataclass(frozen=True, slots=True)
class NordnetClassifiedRow:
    source_row_number: int
    transaction_type: str
    classification: NordnetTransactionClassification


@dataclass(frozen=True, slots=True)
class NordnetTypeClassificationCount:
    transaction_type: str
    classification: NordnetTransactionClassification
    row_count: int


@dataclass(frozen=True, slots=True)
class NordnetParseResult:
    source_row_count: int
    mapped_holdings_row_count: int
    ignored_non_holdings_cashflow_count: int
    ignored_administrative_count: int
    invalid_row_count: int
    unsupported_blocker_count: int
    duplicate_upload_count: int
    duplicate_broker_identity_rows: int
    duplicate_fallback_identity_rows: int
    issues: tuple[NordnetImportIssue, ...]
    mapped_transactions: tuple[NordnetMappedTransaction, ...]
    classified_rows: tuple[NordnetClassifiedRow, ...]

    @property
    def valid_mapped_row_count(self) -> int:
        """Backward-compatible name for mapped Holdings rows."""
        return self.mapped_holdings_row_count

    @property
    def records(self) -> tuple[HoldingTransactionRecord, ...]:
        return tuple(item.record for item in self.mapped_transactions)

    @property
    def type_classification_counts(self) -> tuple[NordnetTypeClassificationCount, ...]:
        counts: dict[tuple[str, NordnetTransactionClassification], int] = {}
        for row in self.classified_rows:
            key = (row.transaction_type, row.classification)
            counts[key] = counts.get(key, 0) + 1
        return tuple(
            NordnetTypeClassificationCount(transaction_type, classification, count)
            for (transaction_type, classification), count in counts.items()
        )

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == 'error' for issue in self.issues)


@dataclass(frozen=True, slots=True)
class NordnetPreviewRow:
    source_row_number: int
    record: HoldingTransactionRecord
    status: Literal['new', 'existing', 'update', 'duplicate_upload', 'conflict', 'unavailable']


@dataclass(frozen=True, slots=True)
class NordnetImportPreview:
    parse_result: NordnetParseResult
    target_schema_ready: bool
    new_row_count: int
    existing_row_count: int
    would_update_count: int
    duplicate_upload_row_count: int
    conflict_row_count: int
    issues: tuple[NordnetImportIssue, ...]
    rows: tuple[NordnetPreviewRow, ...]

    @property
    def mapped_holdings_row_count(self) -> int:
        return self.parse_result.mapped_holdings_row_count

    @property
    def ignored_non_holdings_cashflow_count(self) -> int:
        return self.parse_result.ignored_non_holdings_cashflow_count

    @property
    def ignored_administrative_count(self) -> int:
        return self.parse_result.ignored_administrative_count

    @property
    def invalid_row_count(self) -> int:
        return self.parse_result.invalid_row_count

    @property
    def unsupported_blocker_count(self) -> int:
        return self.parse_result.unsupported_blocker_count

    @property
    def duplicate_upload_count(self) -> int:
        return self.parse_result.duplicate_upload_count

    @property
    def existing_idempotent_count(self) -> int:
        return self.existing_row_count

    @property
    def update_count(self) -> int:
        return self.would_update_count

    @property
    def conflict_count(self) -> int:
        return self.conflict_row_count

    @property
    def has_blocking_errors(self) -> bool:
        return self.parse_result.has_errors or any(issue.severity == 'error' for issue in self.issues)


@dataclass(frozen=True, slots=True)
class NordnetImportWriteResult:
    preview: NordnetImportPreview
    confirmed: bool
    written_row_count: int


def parse_nordnet_export(content: bytes | BinaryIO) -> NordnetParseResult:
    """Parse Nordnet TSV bytes into canonical V2 Holdings records and classifications."""
    raw_bytes, input_issue = _read_content(content)
    if input_issue is not None:
        return _empty_parse_result(input_issue)
    decoded, encoding_issue = _decode_content(raw_bytes)
    if encoding_issue is not None:
        return _empty_parse_result(encoding_issue)

    try:
        source_rows = tuple(csv.reader(StringIO(decoded, newline=''), delimiter='\t'))
    except csv.Error:
        return _empty_parse_result(NordnetImportIssue(
            code='invalid_tabular_data',
            message='Nordnet-filen kan ikke leses som tab-separert data.',
        ))
    if not source_rows:
        return _empty_parse_result(NordnetImportIssue(
            code='missing_header_row',
            message='Nordnet-filen mangler header-rad.',
        ))
    column_indexes, header_issue = _build_column_indexes(source_rows[0])
    if header_issue is not None:
        return _empty_parse_result(header_issue)

    mapped: list[NordnetMappedTransaction] = []
    classified_rows: list[NordnetClassifiedRow] = []
    issues: list[NordnetImportIssue] = []
    invalid_rows = 0
    unsupported_rows = 0
    ignored_non_holdings = 0
    ignored_administrative = 0
    duplicate_upload_rows = 0
    duplicate_broker_rows = 0
    duplicate_fallback_rows = 0
    seen_broker_ids: set[str] = set()
    seen_fallback_keys: set[str] = set()
    for index, values in enumerate(source_rows[1:], start=2):
        if len(values) != len(source_rows[0]):
            invalid_rows += 1
            issues.append(NordnetImportIssue(
                code='invalid_column_count',
                message='Nordnet-raden har ikke samme antall kolonner som header-raden.',
                source_row_number=index,
            ))
            continue
        row = _PositionalNordnetRow(values, column_indexes)
        source_type = _optional_text(row.get('Transaksjonstype'), uppercase=True)
        if source_type is None:
            invalid_rows += 1
            issues.append(NordnetImportIssue(
                code='invalid_row',
                message='Transaksjonstype mangler.',
                source_row_number=index,
            ))
            continue
        mapping = _MAPPED_TRANSACTION_TYPES.get(source_type)
        if mapping is None:
            classification = _IGNORED_TRANSACTION_TYPES.get(source_type)
            if classification is None:
                unsupported_rows += 1
                classified_rows.append(NordnetClassifiedRow(index, source_type, 'UNSUPPORTED_BLOCKER'))
                issues.append(NordnetImportIssue(
                    code='unsupported_transaction_type',
                    message=f'Ustøttet Nordnet-transaksjonstype: {source_type}.',
                    source_row_number=index,
                ))
                continue
            classified_rows.append(NordnetClassifiedRow(index, source_type, classification))
            if classification == 'NON_HOLDINGS_ACCOUNT_CASHFLOW':
                ignored_non_holdings += 1
            else:
                ignored_administrative += 1
            continue

        canonical_type, classification = mapping
        try:
            record = _map_row(row, canonical_type=canonical_type)
        except ValueError as error:
            invalid_rows += 1
            classified_rows.append(NordnetClassifiedRow(index, source_type, classification))
            issues.append(NordnetImportIssue(
                code='invalid_row',
                message=str(error),
                source_row_number=index,
            ))
            continue
        classified_rows.append(NordnetClassifiedRow(index, source_type, classification))
        broker_id = record.transaction.nordnet_transaction_id
        duplicate_broker = bool(broker_id and broker_id in seen_broker_ids)
        duplicate_fallback = record.fallback_key in seen_fallback_keys
        if duplicate_broker or duplicate_fallback:
            duplicate_upload_rows += 1
        if duplicate_broker:
            duplicate_broker_rows += 1
            issues.append(NordnetImportIssue(
                code='duplicate_broker_identity',
                message='Duplisert Nordnet-transaksjons-ID i opplastingen.',
                source_row_number=index,
                severity='warning',
            ))
        if duplicate_fallback:
            duplicate_fallback_rows += 1
            issues.append(NordnetImportIssue(
                code='duplicate_fallback_identity',
                message='Duplisert fallback-identitet i opplastingen.',
                source_row_number=index,
                severity='warning',
            ))
        if broker_id:
            seen_broker_ids.add(broker_id)
        seen_fallback_keys.add(record.fallback_key)
        mapped.append(NordnetMappedTransaction(index, record, classification))

    return NordnetParseResult(
        source_row_count=len(source_rows) - 1,
        mapped_holdings_row_count=len(mapped),
        ignored_non_holdings_cashflow_count=ignored_non_holdings,
        ignored_administrative_count=ignored_administrative,
        invalid_row_count=invalid_rows,
        unsupported_blocker_count=unsupported_rows,
        duplicate_upload_count=duplicate_upload_rows,
        duplicate_broker_identity_rows=duplicate_broker_rows,
        duplicate_fallback_identity_rows=duplicate_fallback_rows,
        issues=tuple(issues),
        mapped_transactions=tuple(mapped),
        classified_rows=tuple(classified_rows),
    )


def dry_run_nordnet_import(
    content: bytes | BinaryIO,
    *,
    target_connection: sqlite3.Connection | None = None,
) -> NordnetImportPreview:
    """Parse and compare a Nordnet export without schema or transaction writes."""
    parsed = parse_nordnet_export(content)
    if target_connection is None:
        return _build_preview(parsed, target_schema_ready=False, existing_brokers={}, existing_fallbacks={})
    if not _holdings_schema_ready(target_connection):
        return _build_preview(
            parsed,
            target_schema_ready=False,
            existing_brokers={},
            existing_fallbacks={},
            extra_issues=(NordnetImportIssue(
                code='target_schema_missing',
                message='V2 Holdings-schema må initialiseres eksplisitt før import.',
            ),),
        )
    broker_ids, fallback_keys = _target_identities(target_connection)
    return _build_preview(
        parsed,
        target_schema_ready=True,
        existing_brokers=broker_ids,
        existing_fallbacks=fallback_keys,
    )


def import_nordnet_transactions(
    content: bytes | BinaryIO,
    *,
    target_connection: sqlite3.Connection,
    confirmed: bool,
) -> NordnetImportWriteResult:
    """Write a Nordnet export only after caller confirmation and explicit schema setup."""
    preview = dry_run_nordnet_import(content, target_connection=target_connection)
    if not confirmed:
        return NordnetImportWriteResult(preview=preview, confirmed=False, written_row_count=0)
    if not preview.target_schema_ready:
        raise ValueError('V2 Holdings schema must be initialized explicitly before Nordnet import.')
    if preview.has_blocking_errors:
        return NordnetImportWriteResult(preview=preview, confirmed=True, written_row_count=0)

    written_rows = 0
    for row in preview.rows:
        if row.status not in {'new', 'update'}:
            continue
        upsert_holding_transaction(target_connection, row.record)
        written_rows += 1
    return NordnetImportWriteResult(preview=preview, confirmed=True, written_row_count=written_rows)


@dataclass(frozen=True, slots=True)
class _PositionalNordnetRow:
    values: list[str]
    column_indexes: dict[str, tuple[int, ...]]

    def get(self, column_name: str, occurrence: int = 0) -> str | None:
        indexes = self.column_indexes.get(column_name, ())
        return self.values[indexes[occurrence]] if len(indexes) > occurrence else None


def _build_column_indexes(
    headers: list[str],
) -> tuple[dict[str, tuple[int, ...]], NordnetImportIssue | None]:
    indexes: dict[str, list[int]] = {}
    for index, header in enumerate(headers):
        indexes.setdefault(str(header).strip(), []).append(index)
    missing_columns = tuple(column for column in REQUIRED_NORDNET_COLUMNS if column not in indexes)
    if missing_columns:
        return {}, NordnetImportIssue(
            code='missing_required_columns',
            message=f'Mangler påkrevde Nordnet-kolonner: {", ".join(missing_columns)}.',
        )
    return {name: tuple(positions) for name, positions in indexes.items()}, None


def _map_row(row: _PositionalNordnetRow, *, canonical_type: str) -> HoldingTransactionRecord:
    shares = _parse_number(row.get('Antall'), 'Antall', blank_default=0.0)
    price = _parse_number(row.get('Kurs'), 'Kurs', blank_default=0.0)
    amount = _parse_number(row.get('Beløp'), 'Beløp', blank_default=0.0)
    fee = _parse_number(row.get('Kurtasje'), 'Kurtasje', blank_default=0.0)
    result = _parse_optional_number(row.get('Resultat'), 'Resultat')
    if canonical_type in {'KJØPT', 'INNLEGG OVERFØRING', 'INNLEGG'}:
        normalized_shares = abs(shares)
    elif canonical_type == 'SALG':
        normalized_shares = -abs(shares)
    else:
        normalized_shares = 0.0

    transaction = HoldingTransaction(
        transaction_type=canonical_type,
        shares=normalized_shares,
        price=price,
        amount=amount,
        trade_date=_parse_required_date(row.get('Handelsdag'), 'Handelsdag'),
        settlement_date=_parse_optional_date(_settlement_value(row)),
        isin=_sanitize_isin(row.get('ISIN')),
        ticker=None,
        instrument_name=_optional_text(row.get('Verdipapir')),
        currency=_optional_text(row.get('Valuta', _PRIMARY_CURRENCY_OCCURRENCE), uppercase=True),
        nordnet_transaction_id=_optional_text(row.get('Id')),
    )
    return HoldingTransactionRecord.from_transaction(
        transaction,
        note=_optional_text(row.get('Transaksjonstekst')),
        fee=fee,
        nordnet_result=result,
    )


def _build_preview(
    parsed: NordnetParseResult,
    *,
    target_schema_ready: bool,
    existing_brokers: dict[str, _ExistingTarget],
    existing_fallbacks: dict[str, _ExistingTarget],
    extra_issues: tuple[NordnetImportIssue, ...] = (),
) -> NordnetImportPreview:
    rows: list[NordnetPreviewRow] = []
    issues: list[NordnetImportIssue] = list(extra_issues)
    new_rows = 0
    existing_rows = 0
    update_rows = 0
    duplicate_upload_rows = 0
    conflict_rows = 0
    seen_brokers: set[str] = set()
    seen_fallbacks: set[str] = set()
    for mapped in parsed.mapped_transactions:
        record = mapped.record
        broker_id = record.transaction.nordnet_transaction_id
        duplicate_upload = record.fallback_key in seen_fallbacks or bool(broker_id and broker_id in seen_brokers)
        if duplicate_upload:
            duplicate_upload_rows += 1
        broker_target = existing_brokers.get(broker_id) if broker_id else None
        fallback_target = existing_fallbacks.get(record.fallback_key)
        if broker_target is not None and fallback_target is not None and broker_target.transaction_id != fallback_target.transaction_id:
            status: Literal['new', 'existing', 'update', 'duplicate_upload', 'conflict', 'unavailable'] = 'conflict'
            conflict_rows += 1
            issues.append(NordnetImportIssue(
                code='conflicting_target_identity',
                message='Nordnet-ID og fallback-identitet peker på ulike eksisterende transaksjoner.',
                source_row_number=mapped.source_row_number,
            ))
        elif not target_schema_ready:
            status = 'unavailable'
        elif duplicate_upload:
            status = 'duplicate_upload'
        elif broker_target is not None:
            if _source_records_equal(record, broker_target.record):
                status = 'existing'
                existing_rows += 1
            else:
                status = 'update'
                update_rows += 1
        elif fallback_target is not None:
            status = 'existing'
            existing_rows += 1
        else:
            status = 'new'
            new_rows += 1
        rows.append(NordnetPreviewRow(mapped.source_row_number, record, status))
        if broker_id:
            seen_brokers.add(broker_id)
        seen_fallbacks.add(record.fallback_key)
    return NordnetImportPreview(
        parse_result=parsed,
        target_schema_ready=target_schema_ready,
        new_row_count=new_rows,
        existing_row_count=existing_rows,
        would_update_count=update_rows,
        duplicate_upload_row_count=duplicate_upload_rows,
        conflict_row_count=conflict_rows,
        issues=tuple(issues),
        rows=tuple(rows),
    )


def _read_content(content: bytes | BinaryIO) -> tuple[bytes, NordnetImportIssue | None]:
    if isinstance(content, bytes):
        return content, None
    try:
        value = content.read()
    except (AttributeError, OSError):
        return b'', NordnetImportIssue('invalid_input', 'Nordnet-filen kan ikke leses.')
    if not isinstance(value, bytes):
        return b'', NordnetImportIssue('invalid_input', 'Nordnet-filen må være binær data.')
    return value, None


def _decode_content(content: bytes) -> tuple[str, NordnetImportIssue | None]:
    for encoding in ('utf-16', 'utf-8'):
        try:
            return content.decode(encoding), None
        except UnicodeError:
            continue
    return '', NordnetImportIssue('unsupported_encoding', 'Nordnet-filen må være UTF-16 eller UTF-8.')


def _empty_parse_result(issue: NordnetImportIssue) -> NordnetParseResult:
    return NordnetParseResult(0, 0, 0, 0, 0, 0, 0, 0, 0, (issue,), (), ())


def _settlement_value(row: _PositionalNordnetRow) -> str | None:
    for column_name in SETTLEMENT_DATE_COLUMNS:
        value = row.get(column_name)
        if value is not None:
            return value
    return None


def _optional_text(value: str | None, *, uppercase: bool = False) -> str | None:
    text = str(value or '').strip()
    if not text:
        return None
    return text.upper() if uppercase else text


def _sanitize_isin(value: str | None) -> str | None:
    isin = _optional_text(value, uppercase=True)
    return isin.split('.', 1)[0].strip() if isin else None


def _parse_number(value: str | None, field_name: str, *, blank_default: float) -> float:
    text = _optional_text(value)
    if text is None:
        return blank_default
    try:
        number = float(text.replace(' ', '').replace(',', '.'))
    except ValueError as error:
        raise ValueError(f'{field_name} er ikke et gyldig tall.') from error
    if not isfinite(number):
        raise ValueError(f'{field_name} må være endelig.')
    return number


def _parse_optional_number(value: str | None, field_name: str) -> float | None:
    return None if _optional_text(value) is None else _parse_number(value, field_name, blank_default=0.0)


def _parse_required_date(value: str | None, field_name: str) -> str:
    parsed = _parse_optional_date(value)
    if parsed is None:
        raise ValueError(f'{field_name} mangler eller er ugyldig.')
    return parsed


def _parse_optional_date(value: str | None) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    for parser in (datetime.fromisoformat,):
        try:
            return parser(text.replace('Z', '+00:00')).date().isoformat()
        except ValueError:
            continue
    for pattern in ('%d.%m.%Y', '%d/%m/%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _holdings_schema_ready(connection: sqlite3.Connection) -> bool:
    table_names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)",
            (HOLDINGS_TRANSACTIONS_TABLE, HOLDINGS_SETTINGS_TABLE),
        ).fetchall()
    }
    return table_names == {HOLDINGS_TRANSACTIONS_TABLE, HOLDINGS_SETTINGS_TABLE}


@dataclass(frozen=True, slots=True)
class _ExistingTarget:
    transaction_id: int
    record: HoldingTransactionRecord


def _source_records_equal(
    incoming: HoldingTransactionRecord,
    existing: HoldingTransactionRecord,
) -> bool:
    return (
        incoming.transaction == existing.transaction
        and incoming.ticker_source == existing.ticker_source
        and incoming.note == existing.note
        and incoming.fee == existing.fee
        and incoming.nordnet_result == existing.nordnet_result
        and incoming.source_cost_basis_missing == existing.source_cost_basis_missing
    )


def _target_identities(
    connection: sqlite3.Connection,
) -> tuple[dict[str, _ExistingTarget], dict[str, _ExistingTarget]]:
    rows = connection.execute(
        f'SELECT transaction_id, nordnet_transaction_id, fallback_transaction_key FROM {HOLDINGS_TRANSACTIONS_TABLE}'
    ).fetchall()
    records_by_fallback = {
        record.fallback_key: record
        for record in load_holding_transactions(connection)
    }
    targets = {
        int(row[0]): _ExistingTarget(
            transaction_id=int(row[0]),
            record=records_by_fallback[str(row[2])],
        )
        for row in rows
    }
    brokers = {
        str(row[1]).strip(): targets[int(row[0])]
        for row in rows
        if str(row[1] or '').strip()
    }
    fallbacks = {str(row[2]): targets[int(row[0])] for row in rows}
    return brokers, fallbacks
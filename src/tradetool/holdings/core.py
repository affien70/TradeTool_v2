"""Pure Holdings transaction and position logic; no signal logic is implemented yet."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from math import isfinite


_PURCHASE_TYPES = frozenset({'KJ\u00d8PT'})
_SALE_TYPES = frozenset({'SALG'})
_TRANSFER_IN_TYPES = frozenset({'INNLEGG OVERF\u00d8RING', 'INNLEGG'})
_CASH_FLOW_TYPES = frozenset({'UTBYTTE', 'PLATTFORMAVGIFT'})
_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class HoldingTransaction:
    transaction_type: str
    shares: float
    price: float | None = None
    amount: float | None = None
    trade_date: date | datetime | str | None = None
    settlement_date: date | datetime | str | None = None
    isin: str | None = None
    ticker: str | None = None
    instrument_name: str | None = None
    currency: str | None = None
    nordnet_transaction_id: str | None = None

    def __post_init__(self) -> None:
        if not _normalize_text(self.transaction_type, uppercase=True):
            raise ValueError('transaction_type must be non-empty.')
        if not isfinite(float(self.shares)):
            raise ValueError('shares must be finite.')
        for field_name, value in (('price', self.price), ('amount', self.amount)):
            if value is not None and not isfinite(float(value)):
                raise ValueError(f'{field_name} must be finite when provided.')


@dataclass(frozen=True, slots=True)
class TransactionIdentity:
    method: str
    key: str


@dataclass(frozen=True, slots=True)
class CanonicalTransaction:
    transaction: HoldingTransaction
    identity: TransactionIdentity
    sequence: int


@dataclass(frozen=True, slots=True)
class CashFlow:
    transaction_type: str
    amount: float


@dataclass(frozen=True, slots=True)
class PositionState:
    open: bool
    open_quantity: float
    priced_open_quantity: float
    remaining_cost_basis: float
    gav: float | None
    cost_basis_status: str | None
    cashflows: tuple[CashFlow, ...] = ()


def fallback_transaction_key(transaction: HoldingTransaction) -> str:
    instrument_identifier = (
        _normalize_text(transaction.isin, uppercase=True)
        or _normalize_text(transaction.ticker, uppercase=True)
        or _normalize_text(transaction.instrument_name)
    )
    key_parts = (
        _normalize_date(transaction.trade_date),
        _normalize_date(transaction.settlement_date),
        instrument_identifier,
        _normalize_text(transaction.transaction_type, uppercase=True),
        _normalize_decimal(transaction.shares),
        _normalize_text(transaction.currency, uppercase=True),
    )
    return sha256('|'.join(key_parts).encode('utf-8')).hexdigest()


def transaction_identity(transaction: HoldingTransaction) -> TransactionIdentity:
    nordnet_transaction_id = _normalize_text(transaction.nordnet_transaction_id)
    if nordnet_transaction_id:
        return TransactionIdentity(method='nordnet_transaction_id', key=nordnet_transaction_id)
    return TransactionIdentity(method='fallback_transaction_key', key=fallback_transaction_key(transaction))


def canonicalize_transactions(transactions: Iterable[HoldingTransaction]) -> tuple[CanonicalTransaction, ...]:
    deduplicated: dict[tuple[str, str], CanonicalTransaction] = {}
    for sequence, transaction in enumerate(transactions):
        identity = transaction_identity(transaction)
        deduplicated[(identity.method, identity.key)] = CanonicalTransaction(
            transaction=transaction,
            identity=identity,
            sequence=sequence,
        )
    return tuple(sorted(deduplicated.values(), key=_transaction_order_key))


def reconstruct_position(transactions: Iterable[HoldingTransaction]) -> PositionState:
    remaining_quantity = 0.0
    priced_quantity = 0.0
    remaining_cost = 0.0
    cashflows: list[CashFlow] = []

    for canonical_transaction in canonicalize_transactions(transactions):
        transaction = canonical_transaction.transaction
        transaction_type = _normalize_text(transaction.transaction_type, uppercase=True)
        quantity = abs(float(transaction.shares))

        if transaction_type in _CASH_FLOW_TYPES:
            cashflows.append(CashFlow(transaction_type=transaction_type, amount=float(transaction.amount or 0.0)))
            continue

        if transaction_type in _PURCHASE_TYPES:
            remaining_quantity += quantity
            priced_quantity += quantity
            remaining_cost += _transaction_cost(transaction, quantity)
            continue

        if transaction_type in _TRANSFER_IN_TYPES:
            remaining_quantity += quantity
            continue

        if transaction_type in _SALE_TYPES:
            reducible_quantity = min(quantity, priced_quantity)
            average_cost_before_sale = remaining_cost / priced_quantity if priced_quantity > _EPSILON else 0.0
            remaining_cost = max(0.0, remaining_cost - reducible_quantity * average_cost_before_sale)
            priced_quantity = max(0.0, priced_quantity - reducible_quantity)
            remaining_quantity = max(0.0, remaining_quantity - quantity)
            if remaining_quantity <= _EPSILON:
                remaining_quantity = 0.0
                priced_quantity = 0.0
                remaining_cost = 0.0

    return _build_position_state(
        remaining_quantity=remaining_quantity,
        priced_quantity=priced_quantity,
        remaining_cost=remaining_cost,
        cashflows=tuple(cashflows),
    )


def _build_position_state(
    *,
    remaining_quantity: float,
    priced_quantity: float,
    remaining_cost: float,
    cashflows: tuple[CashFlow, ...],
) -> PositionState:
    if remaining_quantity <= _EPSILON:
        return PositionState(
            open=False,
            open_quantity=0.0,
            priced_open_quantity=0.0,
            remaining_cost_basis=0.0,
            gav=None,
            cost_basis_status=None,
            cashflows=cashflows,
        )

    unknown_quantity = remaining_quantity - priced_quantity
    if priced_quantity <= _EPSILON:
        return PositionState(
            open=True,
            open_quantity=remaining_quantity,
            priced_open_quantity=0.0,
            remaining_cost_basis=remaining_cost,
            gav=None,
            cost_basis_status='unknown',
            cashflows=cashflows,
        )
    if unknown_quantity > _EPSILON:
        return PositionState(
            open=True,
            open_quantity=remaining_quantity,
            priced_open_quantity=priced_quantity,
            remaining_cost_basis=remaining_cost,
            gav=remaining_cost / priced_quantity,
            cost_basis_status='partially_unknown',
            cashflows=cashflows,
        )
    return PositionState(
        open=True,
        open_quantity=remaining_quantity,
        priced_open_quantity=priced_quantity,
        remaining_cost_basis=remaining_cost,
        gav=remaining_cost / remaining_quantity,
        cost_basis_status='known',
        cashflows=cashflows,
    )


def _transaction_order_key(canonical_transaction: CanonicalTransaction) -> tuple[object, ...]:
    transaction = canonical_transaction.transaction
    trade_date = _normalize_date(transaction.trade_date)
    settlement_date = _normalize_date(transaction.settlement_date)
    if trade_date or settlement_date:
        return (0, trade_date, settlement_date, fallback_transaction_key(transaction), canonical_transaction.sequence)
    return (1, '', '', '', canonical_transaction.sequence)


def _transaction_cost(transaction: HoldingTransaction, quantity: float) -> float:
    if transaction.amount is not None and abs(float(transaction.amount)) > _EPSILON:
        return abs(float(transaction.amount))
    return quantity * float(transaction.price or 0.0)


def _normalize_text(value: object, *, uppercase: bool = False) -> str:
    normalized = ' '.join(str(value or '').strip().split())
    return normalized.upper() if uppercase else normalized


def _normalize_date(value: date | datetime | str | None) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _normalize_text(value)
    if not text:
        return ''
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).date().isoformat()
    except ValueError:
        return text


def _normalize_decimal(value: object) -> str:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError('shares must be numeric.') from error
    if not decimal_value.is_finite():
        raise ValueError('shares must be finite.')
    if decimal_value == 0:
        return '0'
    return format(decimal_value.normalize(), 'f').rstrip('0').rstrip('.')
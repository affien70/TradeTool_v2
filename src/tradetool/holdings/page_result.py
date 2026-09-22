"""Read-only Holdings page result composed from existing engine contracts."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from pathlib import Path

from tradetool.data import ReadOnlySQLite, load_price_history_v2_for_tickers
from tradetool.features import (
    AtrTrailingStop,
    atr_trailing_stop,
    average_true_range,
    holdings_relative_strength,
    post_entry_peak,
    simple_moving_average,
)
from tradetool.features.raw import compute_raw_features
from tradetool.holdings.core import PositionState, reconstruct_position
from tradetool.holdings.market_data import (
    HoldingMarketDataResult,
    build_holding_signal_inputs_from_v2_price_history,
)
from tradetool.holdings.signals import HoldingSignalEvaluation, HoldingSignalRules, evaluate_holding_signal
from tradetool.holdings.storage import (
    HoldingSettings,
    HoldingTransactionRecord,
    load_holding_settings,
    load_holding_transactions,
)


@dataclass(frozen=True, slots=True)
class HoldingChartPoint:
    price_date: date
    adjusted_close: float
    volume: float | None
    fast_sma: float | None
    sma200: float | None
    benchmark_adjusted_close: float | None
    indexed_close: float | None
    indexed_benchmark: float | None


@dataclass(frozen=True, slots=True)
class HoldingPurchaseMarker:
    trade_date: date
    transaction_type: str
    quantity: float
    price: float | None
    cost_basis_missing: bool


@dataclass(frozen=True, slots=True)
class HoldingChartDetail:
    ticker: str | None
    benchmark_id: str
    as_of_date: date | None
    points: tuple[HoldingChartPoint, ...]
    purchase_markers: tuple[HoldingPurchaseMarker, ...]
    current_price: float | None
    fast_sma: float | None
    sma200: float | None
    relative_strength: float | None
    short_term_return: float | None
    atr: float | None
    post_entry_peak: float | None
    trailing_stop: AtrTrailingStop | None
    unavailable_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HoldingPositionRow:
    position_key: str
    ticker: str | None
    instrument_name: str | None
    quantity: float
    cost_basis_status: str | None
    gav: float | None
    current_price: float | None
    current_market_value: float | None
    unrealized_pnl_nok: float | None
    unrealized_pnl_pct: float | None
    signal_action: str | None
    signal_reasons: tuple[str, ...]
    market_data_as_of_date: date | None
    unavailable_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HoldingPositionDetail:
    row: HoldingPositionRow
    position: PositionState
    source_transactions: tuple[HoldingTransactionRecord, ...]
    market_data: HoldingMarketDataResult
    signal_evaluation: HoldingSignalEvaluation | None
    chart: HoldingChartDetail


@dataclass(frozen=True, slots=True)
class HoldingsPageSummary:
    open_position_count: int
    total_current_market_value: float | None
    total_known_unrealized_pnl_nok: float | None
    market_value_position_count: int
    known_pnl_position_count: int
    known_cost_basis_position_count: int
    partially_unknown_cost_basis_position_count: int
    unknown_cost_basis_position_count: int
    has_unknown_cost_basis: bool
    hold_count: int
    follow_up_count: int
    sell_count: int
    unavailable_count: int


@dataclass(frozen=True, slots=True)
class HoldingsPageResult:
    holdings_schema_ready: bool
    settings: HoldingSettings
    summary: HoldingsPageSummary
    rows: tuple[HoldingPositionRow, ...]
    details: tuple[HoldingPositionDetail, ...]
    unavailable_reasons: tuple[str, ...] = ()

    def detail_for(self, position_key: str) -> HoldingPositionDetail | None:
        return next((detail for detail in self.details if detail.row.position_key == position_key), None)


def build_holdings_page_result(
    *,
    holdings_connection: object,
    market_data_db_path: str | Path,
    settings: HoldingSettings | None = None,
    data_source: str = 'yahoo',
    max_price_date: date | None = None,
    market_database: ReadOnlySQLite | None = None,
) -> HoldingsPageResult:
    """Build the immutable read-only model consumed by a future Holdings page."""
    resolved_settings = settings or load_holding_settings(holdings_connection)
    try:
        records = load_holding_transactions(holdings_connection)
    except ValueError as error:
        if 'Holdings schema is not initialized' not in str(error):
            raise
        return HoldingsPageResult(
            holdings_schema_ready=False,
            settings=resolved_settings,
            summary=_empty_summary(),
            rows=(),
            details=(),
            unavailable_reasons=('holdings_schema_missing',),
        )

    details: list[HoldingPositionDetail] = []
    for position_key, grouped_records in _group_records(records):
        position = reconstruct_position(record.transaction for record in grouped_records)
        if not position.open:
            continue
        detail = _build_position_detail(
            position_key=position_key,
            records=grouped_records,
            position=position,
            settings=resolved_settings,
            market_data_db_path=market_data_db_path,
            data_source=data_source,
            max_price_date=max_price_date,
            market_database=market_database,
        )
        details.append(detail)

    ordered_details = tuple(sorted(details, key=lambda detail: detail.row.position_key))
    rows = tuple(detail.row for detail in ordered_details)
    page_reasons = () if rows else ('no_open_positions',)
    return HoldingsPageResult(
        holdings_schema_ready=True,
        settings=resolved_settings,
        summary=_summarize(rows),
        rows=rows,
        details=ordered_details,
        unavailable_reasons=page_reasons,
    )


def _build_position_detail(
    *,
    position_key: str,
    records: tuple[HoldingTransactionRecord, ...],
    position: PositionState,
    settings: HoldingSettings,
    market_data_db_path: str | Path,
    data_source: str,
    max_price_date: date | None,
    market_database: ReadOnlySQLite | None,
) -> HoldingPositionDetail:
    ticker = _latest_text(records, 'ticker')
    instrument_name = _latest_text(records, 'instrument_name')
    entry_date = _entry_date(records)
    market_data = _market_data_result(
        market_data_db_path=market_data_db_path,
        ticker=ticker,
        position=position,
        settings=settings,
        entry_date=entry_date,
        data_source=data_source,
        max_price_date=max_price_date,
        market_database=market_database,
    )
    chart = _build_chart_detail(
        ticker=ticker,
        records=records,
        position=position,
        settings=settings,
        market_data_db_path=market_data_db_path,
        data_source=data_source,
        max_price_date=max_price_date,
        market_database=market_database,
        market_data=market_data,
        entry_date=entry_date,
    )
    evaluation = None
    if market_data.ready and market_data.signal_inputs is not None:
        evaluation = evaluate_holding_signal(
            market_data.signal_inputs,
            rules=_signal_rules(settings),
        )

    current_market_value = None
    if chart.current_price is not None:
        current_market_value = chart.current_price * position.open_quantity
    known_basis = position.cost_basis_status == 'known'
    unrealized_pnl_nok = (
        current_market_value - position.remaining_cost_basis
        if known_basis and current_market_value is not None
        else None
    )
    unrealized_pnl_pct = (
        unrealized_pnl_nok / position.remaining_cost_basis
        if unrealized_pnl_nok is not None and position.remaining_cost_basis > 0
        else None
    )
    reasons = _unique((*market_data.unavailable_reasons, *chart.unavailable_reasons))
    row = HoldingPositionRow(
        position_key=position_key,
        ticker=ticker,
        instrument_name=instrument_name,
        quantity=position.open_quantity,
        cost_basis_status=position.cost_basis_status,
        gav=position.gav,
        current_price=chart.current_price,
        current_market_value=current_market_value,
        unrealized_pnl_nok=unrealized_pnl_nok,
        unrealized_pnl_pct=unrealized_pnl_pct,
        signal_action=evaluation.action if evaluation is not None else None,
        signal_reasons=evaluation.reasons if evaluation is not None else (),
        market_data_as_of_date=market_data.as_of_date or chart.as_of_date,
        unavailable_reasons=reasons,
    )
    return HoldingPositionDetail(
        row=row,
        position=position,
        source_transactions=records,
        market_data=market_data,
        signal_evaluation=evaluation,
        chart=chart,
    )


def _market_data_result(
    *,
    market_data_db_path: str | Path,
    ticker: str | None,
    position: PositionState,
    settings: HoldingSettings,
    entry_date: date | None,
    data_source: str,
    max_price_date: date | None,
    market_database: ReadOnlySQLite | None,
) -> HoldingMarketDataResult:
    try:
        return build_holding_signal_inputs_from_v2_price_history(
            db_path=market_data_db_path,
            ticker=ticker,
            position=position,
            settings=settings,
            entry_date=entry_date,
            data_source=data_source,
            max_price_date=max_price_date,
            database=market_database,
        )
    except (FileNotFoundError, ValueError):
        return HoldingMarketDataResult(
            ticker=ticker,
            benchmark_id=settings.norway_benchmark_id.strip().upper(),
            as_of_date=None,
            ready=False,
            signal_inputs=None,
            unavailable_reasons=('market_data_unavailable',),
        )


def _build_chart_detail(
    *,
    ticker: str | None,
    records: tuple[HoldingTransactionRecord, ...],
    position: PositionState,
    settings: HoldingSettings,
    market_data_db_path: str | Path,
    data_source: str,
    max_price_date: date | None,
    market_database: ReadOnlySQLite | None,
    market_data: HoldingMarketDataResult,
    entry_date: date | None,
) -> HoldingChartDetail:
    markers = _purchase_markers(records)
    if not ticker:
        return HoldingChartDetail(
            ticker=None,
            benchmark_id=market_data.benchmark_id,
            as_of_date=None,
            points=(),
            purchase_markers=markers,
            current_price=None,
            fast_sma=None,
            sma200=None,
            relative_strength=None,
            short_term_return=None,
            atr=None,
            post_entry_peak=None,
            trailing_stop=None,
            unavailable_reasons=('ticker_missing',),
        )

    try:
        loaded = load_price_history_v2_for_tickers(
            db_path=str(market_data_db_path),
            tickers=(ticker, market_data.benchmark_id),
            data_source=data_source,
            max_price_date=max_price_date,
            database=market_database,
        )
    except (FileNotFoundError, ValueError):
        return _empty_chart_detail(
            ticker=ticker,
            benchmark_id=market_data.benchmark_id,
            markers=markers,
            reasons=('market_data_unavailable',),
        )

    stock_rows = loaded.rows_by_ticker.get(ticker, ())
    benchmark_rows = loaded.rows_by_ticker.get(market_data.benchmark_id, ())
    reasons: list[str] = []
    if not stock_rows:
        reasons.append('stock_market_data_missing')
    if not benchmark_rows:
        reasons.append('benchmark_market_data_missing')
    if not stock_rows:
        return _empty_chart_detail(
            ticker=ticker,
            benchmark_id=market_data.benchmark_id,
            markers=markers,
            reasons=tuple(reasons),
        )

    closes = tuple(row.adjusted_close for row in stock_rows)
    benchmark_closes = tuple(row.adjusted_close for row in benchmark_rows)
    current_price = _finite_or_none(closes[-1])
    fast_sma = (
        simple_moving_average(closes, window=settings.sell_fast_sma_days)
        if settings.sell_fast_sma_days > 0
        else None
    )
    sma200 = simple_moving_average(closes, window=200)
    relative_strength = holdings_relative_strength(closes, benchmark_closes, months=settings.rs_months)
    features = compute_raw_features(tuple(row.to_feature_record() for row in stock_rows))
    short_term_return = _finite_or_none(features.features['return_1m'])
    atr, peak, trailing_stop = _trailing_stop_values(
        rows=stock_rows,
        position=position,
        settings=settings,
        entry_date=entry_date,
        current_price=current_price,
    )
    points = _chart_points(stock_rows, benchmark_rows, settings)
    return HoldingChartDetail(
        ticker=ticker,
        benchmark_id=market_data.benchmark_id,
        as_of_date=stock_rows[-1].price_date,
        points=points,
        purchase_markers=markers,
        current_price=current_price,
        fast_sma=fast_sma,
        sma200=sma200,
        relative_strength=relative_strength,
        short_term_return=short_term_return,
        atr=atr,
        post_entry_peak=peak,
        trailing_stop=trailing_stop,
        unavailable_reasons=tuple(reasons),
    )


def _trailing_stop_values(*, rows, position: PositionState, settings: HoldingSettings, entry_date: date | None, current_price: float | None) -> tuple[float | None, float | None, AtrTrailingStop | None]:
    if not settings.sell_drop_from_peak or entry_date is None or position.gav is None or current_price is None:
        return None, None, None
    post_entry_rows = tuple(row for row in rows if row.price_date >= entry_date)
    atr = average_true_range(
        tuple(row.high for row in post_entry_rows),
        tuple(row.low for row in post_entry_rows),
        tuple(row.raw_close for row in post_entry_rows),
    )
    peak = post_entry_peak(tuple(row.adjusted_close for row in post_entry_rows))
    if atr is None or peak is None:
        return atr, peak, None
    return atr, peak, atr_trailing_stop(
        close=current_price,
        entry_price=position.gav,
        peak_price=peak,
        atr=atr,
        atr_multiplier=settings.atr_multiplier,
    )


def _chart_points(stock_rows, benchmark_rows, settings: HoldingSettings) -> tuple[HoldingChartPoint, ...]:
    benchmark_by_date = {row.price_date: row.adjusted_close for row in benchmark_rows}
    end_date = stock_rows[-1].price_date
    start_date = _period_start(settings.period_label, end_date)
    visible_rows = tuple(row for row in stock_rows if row.price_date >= start_date)
    aligned_rows = tuple(row for row in visible_rows if row.price_date in benchmark_by_date)
    baseline_stock = _finite_or_none(aligned_rows[0].adjusted_close) if aligned_rows else None
    baseline_benchmark = _finite_or_none(benchmark_by_date[aligned_rows[0].price_date]) if aligned_rows else None
    points: list[HoldingChartPoint] = []
    full_closes = tuple(row.adjusted_close for row in stock_rows)
    for index, row in enumerate(stock_rows):
        if row.price_date < start_date:
            continue
        fast_sma = (
            simple_moving_average(full_closes[:index + 1], window=settings.sell_fast_sma_days)
            if settings.sell_fast_sma_days > 0
            else None
        )
        sma200 = simple_moving_average(full_closes[:index + 1], window=200)
        benchmark_close = _finite_or_none(benchmark_by_date.get(row.price_date))
        close = _finite_or_none(row.adjusted_close)
        indexed_close = _indexed_value(close, baseline_stock) if benchmark_close is not None else None
        indexed_benchmark = _indexed_value(benchmark_close, baseline_benchmark)
        points.append(HoldingChartPoint(
            price_date=row.price_date,
            adjusted_close=float(row.adjusted_close),
            volume=_finite_or_none(row.volume),
            fast_sma=fast_sma,
            sma200=sma200,
            benchmark_adjusted_close=benchmark_close,
            indexed_close=indexed_close,
            indexed_benchmark=indexed_benchmark,
        ))
    return tuple(points)


def _group_records(records: tuple[HoldingTransactionRecord, ...]) -> tuple[tuple[str, tuple[HoldingTransactionRecord, ...]], ...]:
    grouped: dict[str, list[HoldingTransactionRecord]] = defaultdict(list)
    for record in records:
        grouped[_position_key(record)].append(record)
    return tuple((key, tuple(group)) for key, group in grouped.items())


def _position_key(record: HoldingTransactionRecord) -> str:
    transaction = record.transaction
    for prefix, value in (
        ('isin', transaction.isin),
        ('ticker', transaction.ticker),
        ('instrument', transaction.instrument_name),
    ):
        normalized = ' '.join(str(value or '').strip().split())
        if normalized:
            return f'{prefix}:{normalized.upper() if prefix != "instrument" else normalized}'
    return f'fallback:{record.fallback_key}'


def _latest_text(records: tuple[HoldingTransactionRecord, ...], attribute: str) -> str | None:
    for record in reversed(records):
        value = getattr(record.transaction, attribute)
        normalized = str(value or '').strip()
        if normalized:
            return normalized.upper() if attribute == 'ticker' else normalized
    return None


def _entry_date(records: tuple[HoldingTransactionRecord, ...]) -> date | None:
    dates = [
        _coerce_date(record.transaction.trade_date)
        for record in records
        if record.transaction.shares > 0 and _coerce_date(record.transaction.trade_date) is not None
    ]
    return min(dates) if dates else None


def _purchase_markers(records: tuple[HoldingTransactionRecord, ...]) -> tuple[HoldingPurchaseMarker, ...]:
    markers = []
    for record in records:
        transaction = record.transaction
        trade_date = _coerce_date(transaction.trade_date)
        if transaction.shares > 0 and trade_date is not None:
            markers.append(HoldingPurchaseMarker(
                trade_date=trade_date,
                transaction_type=transaction.transaction_type,
                quantity=transaction.shares,
                price=transaction.price,
                cost_basis_missing=record.source_cost_basis_missing,
            ))
    return tuple(markers)


def _signal_rules(settings: HoldingSettings) -> HoldingSignalRules:
    return HoldingSignalRules(
        sell_if_rs_weak=settings.sell_rs_weak,
        sell_if_below_cost_basis=settings.sell_below_cost_basis,
        sell_if_drop_from_peak=settings.sell_drop_from_peak,
        sell_fast_sma_days=settings.sell_fast_sma_days,
        atr_multiplier=settings.atr_multiplier,
        sell_rs_threshold=settings.rs_threshold,
    )


def _summarize(rows: tuple[HoldingPositionRow, ...]) -> HoldingsPageSummary:
    values = tuple(row.current_market_value for row in rows if row.current_market_value is not None)
    pnls = tuple(row.unrealized_pnl_nok for row in rows if row.unrealized_pnl_nok is not None)
    statuses = tuple(row.cost_basis_status for row in rows)
    actions = tuple(row.signal_action for row in rows)
    return HoldingsPageSummary(
        open_position_count=len(rows),
        total_current_market_value=sum(values) if values else None,
        total_known_unrealized_pnl_nok=sum(pnls) if pnls else None,
        market_value_position_count=len(values),
        known_pnl_position_count=len(pnls),
        known_cost_basis_position_count=statuses.count('known'),
        partially_unknown_cost_basis_position_count=statuses.count('partially_unknown'),
        unknown_cost_basis_position_count=statuses.count('unknown'),
        has_unknown_cost_basis=('partially_unknown' in statuses or 'unknown' in statuses),
        hold_count=actions.count('HOLD'),
        follow_up_count=actions.count('FØLG MED'),
        sell_count=actions.count('SELL'),
        unavailable_count=actions.count(None),
    )


def _empty_summary() -> HoldingsPageSummary:
    return _summarize(())


def _empty_chart_detail(*, ticker: str, benchmark_id: str, markers: tuple[HoldingPurchaseMarker, ...], reasons: tuple[str, ...]) -> HoldingChartDetail:
    return HoldingChartDetail(
        ticker=ticker,
        benchmark_id=benchmark_id,
        as_of_date=None,
        points=(),
        purchase_markers=markers,
        current_price=None,
        fast_sma=None,
        sma200=None,
        relative_strength=None,
        short_term_return=None,
        atr=None,
        post_entry_peak=None,
        trailing_stop=None,
        unavailable_reasons=reasons,
    )


def _period_start(period_label: str, end_date: date) -> date:
    years = {'1 år': 1, '2 år': 2, '5 år': 5}[period_label]
    try:
        return date(end_date.year - years, end_date.month, end_date.day)
    except ValueError:
        return date(end_date.year - years, end_date.month, 28)


def _coerce_date(value: date | datetime | str | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _finite_or_none(value: object) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    return numeric if isfinite(numeric) else None


def _indexed_value(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline is None or baseline == 0:
        return None
    return value / baseline * 100.0


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))
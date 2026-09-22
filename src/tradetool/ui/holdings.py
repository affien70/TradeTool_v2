"""Presentation formatting for the read-only Holdings page result."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from tradetool.holdings import HoldingPositionRow, HoldingsPageResult


@dataclass(frozen=True, slots=True)
class HoldingsSummaryDisplay:
    open_position_count: str
    total_current_market_value: str
    known_unrealized_pnl: str
    signal_counts: str
    cost_basis_notice: str | None


def build_holdings_summary_display(result: HoldingsPageResult) -> HoldingsSummaryDisplay:
    """Format engine-provided portfolio totals for compact Streamlit metrics."""
    summary = result.summary
    return HoldingsSummaryDisplay(
        open_position_count=str(summary.open_position_count),
        total_current_market_value=format_nok(summary.total_current_market_value),
        known_unrealized_pnl=format_nok(summary.total_known_unrealized_pnl_nok),
        signal_counts=(
            f'HOLD: {summary.hold_count} | FØLG MED: {summary.follow_up_count} | '
            f'SELL: {summary.sell_count} | Utilgjengelig: {summary.unavailable_count}'
        ),
        cost_basis_notice=_cost_basis_notice(result),
    )


def build_holdings_table_rows(result: HoldingsPageResult) -> list[dict[str, str]]:
    """Build display rows without changing any values supplied by the engine."""
    return [build_holding_table_row(row) for row in result.rows]


def build_holding_table_row(row: HoldingPositionRow) -> dict[str, str]:
    basis_known = row.cost_basis_status == 'known'
    return {
        'Ticker': row.ticker or 'Ukjent',
        'Instrument': row.instrument_name or 'Ukjent',
        'Antall': format_quantity(row.quantity),
        'GAV': format_nok(row.gav) if basis_known else _cost_basis_label(row.cost_basis_status),
        'Siste pris': format_nok(row.current_price),
        'Markedsverdi': format_nok(row.current_market_value),
        'Urealisert P/L NOK': format_nok(row.unrealized_pnl_nok) if basis_known else _cost_basis_label(row.cost_basis_status),
        'Urealisert P/L %': format_percentage(row.unrealized_pnl_pct) if basis_known else _cost_basis_label(row.cost_basis_status),
        'Kostgrunnlag': _cost_basis_label(row.cost_basis_status),
        'Signal': row.signal_action or 'Utilgjengelig',
        'Forklaring': _row_explanation(row),
        'Markedsdato': format_date(row.market_data_as_of_date),
    }


def page_state_message(result: HoldingsPageResult) -> str | None:
    if not result.holdings_schema_ready:
        return 'Beholdningslageret er ikke klart ennå.'
    if not result.rows:
        return 'Ingen åpne beholdninger å vise.'
    return None


def format_nok(value: float | None) -> str:
    if value is None:
        return 'Ukjent'
    return f'{value:,.2f} kr'.replace(',', ' ').replace('.', ',')


def format_percentage(value: float | None) -> str:
    if value is None:
        return 'Ukjent'
    return f'{value:.2%}'.replace('.', ',')


def format_quantity(value: float) -> str:
    return f'{value:,.4f}'.rstrip('0').rstrip('.').replace(',', ' ').replace('.', ',')


def format_date(value: date | None) -> str:
    return value.strftime('%d.%m.%Y') if value is not None else 'Ukjent'


def _cost_basis_notice(result: HoldingsPageResult) -> str | None:
    summary = result.summary
    if not summary.has_unknown_cost_basis:
        return None
    parts = ['Urealisert P/L omfatter bare posisjoner med kjent kostgrunnlag.']
    if summary.partially_unknown_cost_basis_position_count:
        parts.append(f'{summary.partially_unknown_cost_basis_position_count} har delvis kjent kostgrunnlag.')
    if summary.unknown_cost_basis_position_count:
        parts.append(f'{summary.unknown_cost_basis_position_count} har ukjent kostgrunnlag.')
    return ' '.join(parts)


def _cost_basis_label(status: str | None) -> str:
    labels = {
        'known': 'Kjent',
        'partially_unknown': 'Delvis kjent',
        'unknown': 'Ukjent',
    }
    return labels.get(status, 'Ukjent')


def _row_explanation(row: HoldingPositionRow) -> str:
    reasons = row.signal_reasons if row.signal_action else row.unavailable_reasons
    if not reasons:
        return 'Ingen forklaring tilgjengelig'
    return '; '.join(_unavailable_reason_label(reason) for reason in reasons)


def _unavailable_reason_label(reason: str) -> str:
    labels = {
        'benchmark_market_data_missing': 'Mangler benchmarkdata',
        'holdings_schema_missing': 'Beholdningslager mangler',
        'market_data_unavailable': 'Markedsdata er utilgjengelig',
        'stock_market_data_missing': 'Mangler markedsdata for ticker',
        'ticker_missing': 'Ticker mangler',
    }
    return labels.get(reason, reason)
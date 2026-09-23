"""Presentation formatting for the read-only Holdings page result."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from plotly import graph_objects as go
from plotly.subplots import make_subplots

from tradetool.holdings import HoldingPositionDetail, HoldingPositionRow, HoldingsPageResult


@dataclass(frozen=True, slots=True)
class HoldingsSummaryDisplay:
    open_position_count: str
    total_current_market_value: str
    known_unrealized_pnl: str
    signal_counts: str
    cost_basis_notice: str | None


@dataclass(frozen=True, slots=True)
class HoldingsDetailDisplay:
    position_key: str
    label: str
    ticker: str
    quantity: str
    gav: str
    cost_basis_status: str
    current_price: str
    current_market_value: str
    unrealized_pnl_nok: str
    unrealized_pnl_pct: str
    signal: str
    reasons: tuple[str, ...]
    market_data_as_of_date: str
    relative_strength: str
    short_term_return: str
    atr: str | None
    post_entry_peak: str | None
    trailing_stop: str | None
    chart_unavailable_reasons: tuple[str, ...]


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


def holding_position_options(result: HoldingsPageResult) -> tuple[tuple[str, str], ...]:
    """Return engine position keys with display labels for the selector."""
    return tuple((row.position_key, _position_label(row)) for row in result.rows)


def build_holdings_detail_display(detail: HoldingPositionDetail) -> HoldingsDetailDisplay:
    """Format engine-owned detail values without deriving any portfolio or chart data."""
    row = detail.row
    table_row = build_holding_table_row(row)
    chart = detail.chart
    reasons = row.signal_reasons if row.signal_action else _formatted_reasons(row.unavailable_reasons)
    trailing_stop = None
    if chart.trailing_stop is not None:
        trailing_stop = (
            f'Aktiv: {format_nok(chart.trailing_stop.stop_price)}'
            if chart.trailing_stop.active
            else 'Ikke aktiv'
        )
    return HoldingsDetailDisplay(
        position_key=row.position_key,
        label=_position_label(row),
        ticker=row.ticker or 'Ukjent',
        quantity=table_row['Antall'],
        gav=table_row['GAV'],
        cost_basis_status=table_row['Kostgrunnlag'],
        current_price=table_row['Siste pris'],
        current_market_value=table_row['Markedsverdi'],
        unrealized_pnl_nok=table_row['Urealisert P/L NOK'],
        unrealized_pnl_pct=table_row['Urealisert P/L %'],
        signal=table_row['Signal'],
        reasons=reasons,
        market_data_as_of_date=table_row['Markedsdato'],
        relative_strength=format_ratio(chart.relative_strength),
        short_term_return=format_percentage(chart.short_term_return),
        atr=format_nok(chart.atr) if chart.atr is not None else None,
        post_entry_peak=format_nok(chart.post_entry_peak) if chart.post_entry_peak is not None else None,
        trailing_stop=trailing_stop,
        chart_unavailable_reasons=_formatted_reasons(chart.unavailable_reasons),
    )


def build_holding_chart_figure(detail: HoldingPositionDetail) -> go.Figure | None:
    """Create a Plotly figure using only values supplied by the engine chart detail."""
    chart = detail.chart
    if not chart.points:
        return None

    dates = [point.price_date for point in chart.points]
    show_volume = any(point.volume is not None for point in chart.points)
    show_benchmark = any(
        point.indexed_close is not None and point.indexed_benchmark is not None
        for point in chart.points
    )
    rows = 1 + int(show_volume) + int(show_benchmark)
    row_heights = [0.65] if rows == 1 else ([0.55, 0.2] if rows == 2 else [0.55, 0.2, 0.25])
    figure = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=row_heights,
    )
    figure.add_trace(
        go.Scatter(
            x=dates,
            y=[point.adjusted_close for point in chart.points],
            mode='lines',
            name='Kurs',
            customdata=[point.volume for point in chart.points],
            hovertemplate='Dato: %{x|%d.%m.%Y}<br>Kurs: %{y:.2f}<br>Volum: %{customdata}<extra></extra>',
        ),
        row=1,
        col=1,
    )
    if any(point.fast_sma is not None for point in chart.points):
        figure.add_trace(
            go.Scatter(
                x=dates,
                y=[point.fast_sma for point in chart.points],
                mode='lines',
                name='SMA rask',
            ),
            row=1,
            col=1,
        )
    if any(point.sma200 is not None for point in chart.points):
        figure.add_trace(
            go.Scatter(
                x=dates,
                y=[point.sma200 for point in chart.points],
                mode='lines',
                name='SMA200',
            ),
            row=1,
            col=1,
        )
    marker_points = [marker for marker in chart.purchase_markers if marker.price is not None]
    if marker_points:
        figure.add_trace(
            go.Scatter(
                x=[marker.trade_date for marker in marker_points],
                y=[marker.price for marker in marker_points],
                mode='markers',
                name='Kjøp',
                marker={'symbol': 'triangle-up', 'size': 10},
                text=[_purchase_marker_label(marker) for marker in marker_points],
                hovertemplate='%{text}<extra></extra>',
            ),
            row=1,
            col=1,
        )
    if chart.post_entry_peak is not None:
        figure.add_hline(y=chart.post_entry_peak, line_dash='dot', annotation_text='Topp etter kjøp', row=1, col=1)
    if chart.trailing_stop is not None and chart.trailing_stop.active and chart.trailing_stop.stop_price is not None:
        figure.add_hline(y=chart.trailing_stop.stop_price, line_dash='dash', annotation_text='ATR-stopp', row=1, col=1)

    next_row = 2
    if show_volume:
        figure.add_trace(
            go.Bar(x=dates, y=[point.volume for point in chart.points], name='Volum'),
            row=next_row,
            col=1,
        )
        figure.update_yaxes(title_text='Volum', row=next_row, col=1)
        next_row += 1
    if show_benchmark:
        figure.add_trace(
            go.Scatter(
                x=dates,
                y=[point.indexed_close for point in chart.points],
                mode='lines',
                name=f'{chart.ticker or "Kurs"} (indeksert)',
            ),
            row=next_row,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=dates,
                y=[point.indexed_benchmark for point in chart.points],
                mode='lines',
                name=f'{chart.benchmark_id} (indeksert)',
            ),
            row=next_row,
            col=1,
        )
        figure.update_yaxes(title_text='Indeksert', row=next_row, col=1)

    figure.update_layout(height=720, hovermode='x unified', margin={'l': 70, 'r': 30, 't': 30, 'b': 50})
    figure.update_yaxes(title_text='Pris', row=1, col=1)
    figure.update_xaxes(title_text='Dato', row=rows, col=1)
    return figure


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


def format_ratio(value: float | None) -> str:
    if value is None:
        return 'Ukjent'
    return f'{value:.3f}'.replace('.', ',')


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


def _position_label(row: HoldingPositionRow) -> str:
    instrument = row.instrument_name or 'Ukjent instrument'
    return f'{instrument} ({row.ticker})' if row.ticker else instrument


def _formatted_reasons(reasons: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_unavailable_reason_label(reason) for reason in reasons)


def _purchase_marker_label(marker) -> str:
    cost_basis = 'ukjent kostgrunnlag' if marker.cost_basis_missing else 'kjent kostgrunnlag'
    price = format_nok(marker.price)
    return (
        f'{marker.transaction_type}: {format_quantity(marker.quantity)} aksjer<br>'
        f'Pris: {price}<br>{cost_basis}'
    )


def _unavailable_reason_label(reason: str) -> str:
    labels = {
        'benchmark_market_data_missing': 'Mangler benchmarkdata',
        'holdings_schema_missing': 'Beholdningslager mangler',
        'market_data_unavailable': 'Markedsdata er utilgjengelig',
        'stock_market_data_missing': 'Mangler markedsdata for ticker',
        'ticker_missing': 'Ticker mangler',
    }
    return labels.get(reason, reason)
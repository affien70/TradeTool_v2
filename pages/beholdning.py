from __future__ import annotations

import sqlite3

import streamlit as st

from tradetool.config.runtime_settings import inspect_app_database
from tradetool.holdings import build_holdings_page_result
from tradetool.ui.holdings import (
    build_holding_chart_figure,
    build_holdings_detail_display,
    build_holdings_summary_display,
    build_holdings_table_rows,
    holding_position_options,
    page_state_message,
)


def _load_page_result():
    database_status = inspect_app_database()
    if not database_status.exists or not database_status.readable:
        st.warning('Lokal app-database er ikke tilgjengelig for lesing.')
        return None
    try:
        with sqlite3.connect(f'file:{database_status.configured_path}?mode=ro', uri=True) as connection:
            return build_holdings_page_result(
                holdings_connection=connection,
                market_data_db_path=database_status.configured_path,
            )
    except (OSError, sqlite3.Error, ValueError):
        st.warning('Beholdninger kan ikke leses akkurat nå.')
        return None


def _render_summary(result) -> None:
    display = build_holdings_summary_display(result)
    st.subheader('Porteføljeoversikt')
    columns = st.columns(3)
    columns[0].metric('Åpne posisjoner', display.open_position_count)
    columns[1].metric('Markedsverdi', display.total_current_market_value)
    columns[2].metric('Urealisert P/L (kjent kostgrunnlag)', display.known_unrealized_pnl)
    st.caption(display.signal_counts)
    if display.cost_basis_notice:
        st.warning(display.cost_basis_notice)


def _render_active_holdings(result) -> None:
    st.subheader('Aktive beholdninger')
    st.dataframe(
        build_holdings_table_rows(result),
        use_container_width=True,
        hide_index=True,
        column_config={
            'Instrument': st.column_config.TextColumn('Instrument', width='medium'),
            'Forklaring': st.column_config.TextColumn('Forklaring', width='large'),
        },
    )


def _render_selected_position(result) -> None:
    options = holding_position_options(result)
    labels = dict(options)
    selected_position_key = st.selectbox(
        'Velg beholdning',
        options=[position_key for position_key, _ in options],
        format_func=labels.__getitem__,
        key='holdings_selected_position',
    )
    detail = result.detail_for(selected_position_key)
    if detail is None:
        st.warning('Valgt beholdning er ikke tilgjengelig.')
        return

    display = build_holdings_detail_display(detail)
    st.subheader(display.label)
    st.caption(
        f'{display.ticker} | Kostgrunnlag: {display.cost_basis_status} | '
        f'Markedsdata: {display.market_data_as_of_date}'
    )
    first_metrics = st.columns(4)
    first_metrics[0].metric('Antall', display.quantity)
    first_metrics[1].metric('GAV', display.gav)
    first_metrics[2].metric('Siste pris', display.current_price)
    first_metrics[3].metric('Markedsverdi', display.current_market_value)
    second_metrics = st.columns(4)
    second_metrics[0].metric('Urealisert P/L', display.unrealized_pnl_nok)
    second_metrics[1].metric('Urealisert P/L %', display.unrealized_pnl_pct)
    second_metrics[2].metric('Signal', display.signal)
    second_metrics[3].metric('RS', display.relative_strength)

    technical_values = [f'1 mnd: {display.short_term_return}']
    if display.atr is not None:
        technical_values.append(f'ATR: {display.atr}')
    if display.post_entry_peak is not None:
        technical_values.append(f'Topp etter kjøp: {display.post_entry_peak}')
    if display.trailing_stop is not None:
        technical_values.append(f'ATR-stopp: {display.trailing_stop}')
    st.caption(' | '.join(technical_values))
    if display.reasons:
        st.markdown('\n'.join(f'- {reason}' for reason in display.reasons))
    if display.chart_unavailable_reasons:
        st.warning(f'Grafdata: {"; ".join(display.chart_unavailable_reasons)}')

    figure = build_holding_chart_figure(detail)
    if figure is None:
        st.warning('Mangler grafdata for valgt beholdning.')
        return
    st.plotly_chart(
        figure,
        width='stretch',
        key=f'holdings_detail_chart_{display.position_key}',
    )


def render() -> None:
    st.title('Holdings')
    result = _load_page_result()
    if result is None:
        return

    state_message = page_state_message(result)
    if state_message:
        st.info(state_message)
        return

    _render_summary(result)
    _render_active_holdings(result)
    _render_selected_position(result)


render()

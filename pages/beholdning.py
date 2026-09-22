from __future__ import annotations

import sqlite3

import streamlit as st

from tradetool.config.runtime_settings import inspect_app_database
from tradetool.holdings import build_holdings_page_result
from tradetool.ui.holdings import (
    build_holdings_summary_display,
    build_holdings_table_rows,
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


render()

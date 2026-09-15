from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st

from tradetool.config.runtime_settings import inspect_app_database
from tradetool.ui.screener import (
    build_incumbent_screener_ui_result,
    build_selected_ticker_chart_detail,
    incumbent_candidate_detail_rows,
    incumbent_screener_eligible_table_rows,
    incumbent_screener_table_rows,
    selected_incumbent_candidate,
)

UNIVERSE_BENCHMARKS = {
    'NORWAY_V2': 'OSEBX.OL',
    'SP500': '^GSPC',
}
DATA_SOURCE_OPTIONS = ('yahoo',)
MISSING_DB_MESSAGE = 'Ingen lokal app-database funnet. Gå til Innstillinger eller bygg lokal database før screening.'


def _show_database_status() -> object:
    status = inspect_app_database()
    status_columns = st.columns(4)
    status_columns[0].metric('Database', status.configured_path_text)
    status_columns[1].metric('Finnes', 'ja' if status.exists else 'nei')
    status_columns[2].metric('Lesbar', 'ja' if status.readable else 'nei')
    status_columns[3].metric('price_history_v2', 'ja' if status.price_history_v2_table_exists else 'nei')
    if not status.exists:
        st.warning(MISSING_DB_MESSAGE)
    elif not status.readable:
        st.warning(f'Lokal app-database kan ikke leses: {status.error or status.configured_path_text}')
    elif not status.price_history_v2_table_exists:
        st.warning('Lokal app-database mangler tabellen price_history_v2.')
    else:
        st.caption(f'price_history_v2-rader: {status.row_count}. Siste prisdato: {status.latest_price_date}.')
    return status


def _database_ready(status: object) -> bool:
    return bool(status.exists and status.readable and status.price_history_v2_table_exists)


def _render_price_chart(chart_detail) -> None:
    st.vega_lite_chart(
        {'values': [point.to_dict() for point in chart_detail.price_points]},
        {
            'mark': {'type': 'line'},
            'encoding': {
                'x': {'field': 'price_date', 'type': 'temporal', 'title': 'Dato'},
                'y': {'field': 'value', 'type': 'quantitative', 'title': 'Pris'},
                'color': {'field': 'series', 'type': 'nominal', 'title': 'Serie'},
            },
            'transform': [
                {'fold': ['close', 'sma50', 'sma200'], 'as': ['series', 'value']},
                {'filter': 'isValid(datum.value)'},
            ],
            'height': 300,
        },
        use_container_width=True,
    )


def _render_relative_strength_chart(chart_detail) -> None:
    if chart_detail.warning:
        st.warning(chart_detail.warning)
    st.vega_lite_chart(
        {'values': [point.to_dict() for point in chart_detail.price_points]},
        {
            'vconcat': [
                {
                    'mark': {'type': 'line'},
                    'encoding': {
                        'x': {'field': 'price_date', 'type': 'temporal', 'title': 'Dato'},
                        'y': {'field': 'value', 'type': 'quantitative', 'title': 'Indeksert verdi'},
                        'color': {'field': 'series', 'type': 'nominal', 'title': 'Serie'},
                    },
                    'transform': [
                        {'fold': ['indexed_close', 'indexed_benchmark'], 'as': ['series', 'value']},
                        {'filter': 'isValid(datum.value)'},
                    ],
                    'height': 210,
                },
                {
                    'mark': {'type': 'line', 'color': '#f97316'},
                    'encoding': {
                        'x': {'field': 'price_date', 'type': 'temporal', 'title': 'Dato'},
                        'y': {'field': 'relative_strength_line', 'type': 'quantitative', 'title': 'RS-linje'},
                    },
                    'transform': [{'filter': 'isValid(datum.relative_strength_line)'}],
                    'height': 150,
                },
            ]
        },
        use_container_width=True,
    )


def render() -> None:
    st.title('Screener')
    st.caption('Beslutningsstøtte for aksjeutvalg. Kandidater velges av incumbent screener-kjernen; risikotagger er informasjon, ikke filtre.')
    db_status = _show_database_status()

    st.subheader('Aksje-screener')
    control_left, control_right = st.columns([1.6, 1.0])
    with control_left:
        universe_id = st.selectbox('Univers', options=list(UNIVERSE_BENCHMARKS), index=0, key='incumbent_universe_id')
        benchmark_ticker = UNIVERSE_BENCHMARKS[universe_id]
        data_source = st.selectbox('Datakilde', options=list(DATA_SOURCE_OPTIONS), index=0, key='incumbent_data_source')
    with control_right:
        as_of_date = st.date_input('As-of date', value=date.today(), key='incumbent_as_of_date')
        top_n = int(st.number_input('Top N', min_value=1, max_value=100, value=10, step=1, key='incumbent_top_n'))
        run_clicked = st.button('Kjør screener', key='incumbent_run_button', type='primary')
    st.caption(f'Benchmark: {benchmark_ticker} | Motor: incumbent_naive_rs_6m_top_10_v0 | Close: adjusted_close')

    if run_clicked:
        if not _database_ready(db_status):
            st.warning(MISSING_DB_MESSAGE)
            return
        try:
            incumbent_result = build_incumbent_screener_ui_result(
                db_path=Path(db_status.configured_path),
                universe_id=universe_id,
                benchmark_ticker=benchmark_ticker,
                as_of_date=as_of_date,
                data_source=data_source,
                top_n=top_n,
            )
            st.session_state['incumbent_screener_result'] = incumbent_result
            st.session_state['screener_chart_ticker'] = (
                str(incumbent_result.top_candidates[0].get('ticker') or '') if incumbent_result.top_candidates else ''
            )
        except Exception as exc:  # pragma: no cover
            st.error(str(exc))
            return

    incumbent_result = st.session_state.get('incumbent_screener_result')
    if incumbent_result is None:
        st.info('Velg univers og klikk Kjør screener for å vise kandidater.')
        return

    st.subheader('Kjøreoppsummering')
    summary_columns = st.columns(5)
    summary_columns[0].metric('Univers', incumbent_result.universe_id)
    summary_columns[1].metric('Benchmark', incumbent_result.benchmark_ticker)
    summary_columns[2].metric('Effektiv dato', incumbent_result.effective_feature_date or incumbent_result.as_of_date)
    summary_columns[3].metric('Rangert', incumbent_result.eligible_count)
    summary_columns[4].metric('Valgt', incumbent_result.selected_count)

    table_rows = incumbent_screener_table_rows(incumbent_result)
    st.subheader('Rangerte kandidater')
    st.caption('Klikk på en rad for å analysere kandidaten under med samme grafoppsett som i gamle Screener. Risikotagger er informasjon, ikke filtre.')
    event = st.dataframe(
        table_rows,
        use_container_width=True,
        hide_index=True,
        on_select='rerun',
        selection_mode='single-row',
        key='incumbent_screener_results_table',
    )
    selected_rows = []
    if hasattr(event, 'selection') and isinstance(event.selection, dict):
        selected_rows = event.selection.get('rows', []) or []
    if selected_rows and 0 <= int(selected_rows[0]) < len(table_rows):
        st.session_state['screener_chart_ticker'] = str(table_rows[int(selected_rows[0])].get('Ticker') or '')
    elif not st.session_state.get('screener_chart_ticker') and table_rows:
        st.session_state['screener_chart_ticker'] = str(table_rows[0].get('Ticker') or '')

    selected_ticker = str(st.session_state.get('screener_chart_ticker') or '').strip()
    if not selected_ticker and table_rows:
        selected_ticker = str(table_rows[0].get('Ticker') or '').strip()
    st.subheader('Valgt kandidat')
    if not selected_ticker:
        st.warning('Ingen rader tilgjengelig med gjeldende filter.')
        return

    selected_row = selected_incumbent_candidate(incumbent_result, ticker=selected_ticker)
    detail_columns = st.columns(4)
    detail_columns[0].metric('Ticker', selected_row.get('ticker'))
    detail_columns[1].metric('Incumbent-rang', selected_row.get('incumbent_rank'))
    detail_columns[2].metric('Risiko', selected_row.get('risk_level'))
    detail_columns[3].metric('Pris', f"{float(selected_row.get('close')):.2f}" if selected_row.get('close') is not None else '')
    st.markdown(str(selected_row.get('risk_explanation_no') or ''))
    st.dataframe(incumbent_candidate_detail_rows(selected_row), use_container_width=True, hide_index=True)

    chart_detail = st.cache_data(show_spinner=False)(build_selected_ticker_chart_detail)(
        db_path=Path(db_status.configured_path),
        ticker=selected_ticker,
        benchmark_ticker=incumbent_result.benchmark_ticker,
        price_table='price_history_v2',
        data_source=incumbent_result.data_source,
    )
    st.subheader('Prischart')
    _render_price_chart(chart_detail)
    st.subheader('Benchmark og relativ styrke')
    _render_relative_strength_chart(chart_detail)

    with st.expander('Tekniske detaljer', expanded=False):
        st.dataframe(incumbent_screener_eligible_table_rows(incumbent_result), use_container_width=True, hide_index=True)
        st.dataframe(list(incumbent_result.rejections), use_container_width=True, hide_index=True)
        st.dataframe(
            [
                {'felt': 'baseline_id', 'verdi': incumbent_result.baseline_id},
                {'felt': 'universe_source', 'verdi': incumbent_result.universe_source},
                {'felt': 'requested_stock_ticker_count', 'verdi': incumbent_result.requested_stock_ticker_count},
                {'felt': 'close_input_source', 'verdi': incumbent_result.close_input_source},
                {'felt': 'risk_tags_are_filters', 'verdi': False},
            ],
            use_container_width=True,
            hide_index=True,
        )
    return


render()

from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st

from tradetool.config.runtime_settings import inspect_app_database
from tradetool.ui.screener import (
    CHART_PERIOD_CALENDAR_MONTHS,
    DEFAULT_CHART_PERIOD_LABEL,
    build_incumbent_screener_ui_result,
    build_price_chart_spec,
    build_relative_strength_chart_spec,
    build_selected_ticker_chart_detail,
    incumbent_candidate_explanation,
    incumbent_candidate_detail_rows,
    incumbent_screener_eligible_table_rows,
    incumbent_screener_summary_rows,
    incumbent_screener_table_rows,
    incumbent_screener_ticker_options,
    resolve_incumbent_selected_ticker,
    selected_incumbent_candidate,
)

UNIVERSE_BENCHMARKS = {
    'NORWAY_V2': 'OSEBX.OL',
    'SP500': '^GSPC',
}
DATA_SOURCE_OPTIONS = ('yahoo',)
MISSING_DB_MESSAGE = 'Ingen lokal app-database funnet. Gå til Innstillinger eller bygg lokal database før screening.'
SCREENING_DATE_HELP = 'Screeningdato brukes for historisk testing. I vanlig bruk velges siste tilgjengelige prisdato automatisk.'


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


def _default_screening_date(status: object) -> date:
    latest_price_date = getattr(status, 'latest_price_date', None)
    if latest_price_date:
        try:
            return date.fromisoformat(str(latest_price_date))
        except ValueError:
            pass
    return date.today()


def _render_price_chart(chart_detail) -> None:
    spec = build_price_chart_spec(chart_detail)
    if spec is None:
        st.warning(_chart_empty_warning(chart_detail))
        return
    st.vega_lite_chart(
        spec,
        use_container_width=True,
        key=f'price_chart_{chart_detail.ticker}_{chart_detail.chart_period_label}_{chart_detail.requested_end_date}',
    )


def _render_relative_strength_chart(chart_detail) -> None:
    if chart_detail.warning:
        st.warning(chart_detail.warning)
    spec = build_relative_strength_chart_spec(chart_detail)
    if spec is None:
        st.warning(_chart_empty_warning(chart_detail))
        return
    st.vega_lite_chart(
        spec,
        use_container_width=True,
        key=f'rs_chart_{chart_detail.ticker}_{chart_detail.benchmark_ticker}_{chart_detail.chart_period_label}_{chart_detail.requested_end_date}',
    )


def _chart_empty_warning(chart_detail) -> str:
    return (
        f'Mangler grafdata for {chart_detail.ticker}. '
        f'Benchmark: {chart_detail.benchmark_ticker or "ingen"}. '
        f'Forespurt periode: {chart_detail.requested_start_date or "ukjent"} til '
        f'{chart_detail.requested_end_date or "ukjent"}. '
        f'Rader funnet for ticker: {chart_detail.ticker_rows_found}. '
        f'Rader funnet for benchmark: {chart_detail.benchmark_rows_found}.'
    )


@st.cache_data(show_spinner=False)
def _cached_selected_ticker_chart_detail(
    *,
    db_path_text: str,
    selected_ticker: str,
    benchmark_ticker: str,
    data_source: str,
    as_of_date_text: str,
    chart_period_label: str,
):
    return build_selected_ticker_chart_detail(
        db_path=Path(db_path_text),
        ticker=selected_ticker,
        benchmark_ticker=benchmark_ticker,
        price_table='price_history_v2',
        data_source=data_source,
        max_price_date=date.fromisoformat(as_of_date_text),
        chart_period_label=chart_period_label,
    )


def render() -> None:
    st.title('Aksje-screener')
    st.caption('V1-lignende flyt med V2 incumbent screener-kjerne. Risikotagger er informasjon, ikke filtre.')
    db_status = _show_database_status()
    default_screening_date = _default_screening_date(db_status)

    control_left, control_right = st.columns([1.6, 1.0])
    with control_left:
        universe_id = st.selectbox('Univers', options=list(UNIVERSE_BENCHMARKS), index=0, key='incumbent_universe_id')
        benchmark_ticker = UNIVERSE_BENCHMARKS[universe_id]
        data_source = st.selectbox('Datakilde', options=list(DATA_SOURCE_OPTIONS), index=0, key='incumbent_data_source')
    with control_right:
        top_n = int(st.number_input('Top N', min_value=1, max_value=100, value=10, step=1, key='incumbent_top_n'))
        run_clicked = st.button('Kjør screener', key='incumbent_run_button', type='primary')
    with st.expander('Avansert', expanded=False):
        as_of_date = st.date_input(
            'Screeningdato',
            value=default_screening_date,
            key='incumbent_as_of_date',
            help=SCREENING_DATE_HELP,
        )
        st.caption(SCREENING_DATE_HELP)
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

    summary_columns = st.columns(5)
    for column, row in zip(summary_columns, incumbent_screener_summary_rows(incumbent_result), strict=True):
        column.metric(str(row['felt']), row['verdi'])
    st.caption(f'Bruker markedsdata til og med: {incumbent_result.effective_feature_date or incumbent_result.as_of_date}')

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

    selected_ticker = resolve_incumbent_selected_ticker(
        table_rows,
        current_ticker=str(st.session_state.get('screener_chart_ticker') or ''),
        selected_row_indexes=selected_rows,
        selected_ticker=str(st.session_state.get('screener_selected_ticker_picker') or ''),
    )
    if selected_ticker:
        st.session_state['screener_chart_ticker'] = selected_ticker
        if selected_rows:
            st.session_state['screener_selected_ticker_picker'] = selected_ticker

    ticker_options = incumbent_screener_ticker_options(table_rows)
    if ticker_options:
        selected_index = ticker_options.index(selected_ticker) if selected_ticker in ticker_options else 0
        selected_ticker = st.selectbox('Valgt kandidat', options=ticker_options, index=selected_index, key='screener_selected_ticker_picker')
        st.session_state['screener_chart_ticker'] = selected_ticker

    st.subheader('Valgt kandidat')
    if not selected_ticker:
        st.warning('Ingen rader tilgjengelig med gjeldende filter.')
        return

    selected_row = selected_incumbent_candidate(incumbent_result, ticker=selected_ticker)
    detail_ticker = str(selected_row.get('ticker') or selected_ticker).strip().upper()
    detail_columns = st.columns(4)
    detail_columns[0].metric('Ticker', selected_row.get('ticker'))
    detail_columns[1].metric('Incumbent-rang', selected_row.get('incumbent_rank'))
    detail_columns[2].metric('Risiko', selected_row.get('risk_level'))
    detail_columns[3].metric('Pris', f"{float(selected_row.get('close')):.2f}" if selected_row.get('close') is not None else '')
    st.markdown(incumbent_candidate_explanation(selected_row))
    st.dataframe(incumbent_candidate_detail_rows(selected_row), use_container_width=True, hide_index=True)
    chart_period_options = list(CHART_PERIOD_CALENDAR_MONTHS)
    chart_period_label = st.selectbox(
        'Grafperiode',
        options=chart_period_options,
        index=chart_period_options.index(DEFAULT_CHART_PERIOD_LABEL),
        key='screener_chart_period',
    )

    chart_detail = _cached_selected_ticker_chart_detail(
        db_path_text=str(db_status.configured_path),
        selected_ticker=detail_ticker,
        benchmark_ticker=str(incumbent_result.benchmark_ticker),
        data_source=str(incumbent_result.data_source),
        as_of_date_text=str(incumbent_result.as_of_date),
        chart_period_label=str(chart_period_label),
    )
    st.subheader('Prischart')
    _render_price_chart(chart_detail)
    st.subheader('Normalisert benchmark-sammenligning og relativ styrke')
    st.caption('Indeksert mot siste handelsdag før/ved periodestart for å ligne Nordnet-avkastning.')
    _render_relative_strength_chart(chart_detail)

    with st.expander('Tekniske detaljer', expanded=False):
        st.dataframe(
            [
                {'felt': 'selected_ticker_detail', 'verdi': detail_ticker},
                {'felt': 'selected_ticker_price_chart', 'verdi': chart_detail.ticker},
                {'felt': 'selected_ticker_benchmark_chart', 'verdi': chart_detail.ticker},
                {'felt': 'chart_period', 'verdi': chart_detail.chart_period_label},
                {'felt': 'chart_calendar_start_date', 'verdi': chart_detail.calendar_start_date},
                {'felt': 'chart_baseline_date', 'verdi': chart_detail.baseline_date},
                {'felt': 'chart_baseline_ticker_close', 'verdi': chart_detail.baseline_ticker_close},
                {'felt': 'chart_baseline_benchmark_close', 'verdi': chart_detail.baseline_benchmark_close},
                {'felt': 'chart_first_visible_date', 'verdi': chart_detail.requested_start_date},
                {'felt': 'chart_last_visible_date', 'verdi': chart_detail.requested_end_date},
                {'felt': 'chart_first_close', 'verdi': chart_detail.first_close},
                {'felt': 'chart_last_close', 'verdi': chart_detail.last_close},
                {'felt': 'chart_period_return_pct', 'verdi': chart_detail.period_return_pct},
                {'felt': 'chart_close_source', 'verdi': chart_detail.close_source},
                {'felt': 'chart_raw_rows_loaded', 'verdi': chart_detail.ticker_rows_found},
                {'felt': 'chart_visible_rows', 'verdi': chart_detail.visible_rows},
                {'felt': 'chart_row_count', 'verdi': len(chart_detail.price_points)},
                {'felt': 'chart_first_date', 'verdi': chart_detail.requested_start_date},
                {'felt': 'chart_last_date', 'verdi': chart_detail.requested_end_date},
                {'felt': 'chart_first_normalized_date', 'verdi': chart_detail.first_normalized_date},
                {'felt': 'chart_first_indexed_ticker_value', 'verdi': chart_detail.first_indexed_ticker_value},
                {'felt': 'chart_last_indexed_ticker_value', 'verdi': chart_detail.price_points[-1].indexed_close if chart_detail.price_points else None},
                {'felt': 'chart_first_indexed_benchmark_value', 'verdi': chart_detail.first_indexed_benchmark_value},
                {'felt': 'chart_first_rs_index_value', 'verdi': chart_detail.first_rs_index_value},
                {'felt': 'chart_sma50_non_null_count', 'verdi': chart_detail.sma50_non_null_count},
                {'felt': 'chart_sma200_non_null_count', 'verdi': chart_detail.sma200_non_null_count},
            ],
            use_container_width=True,
            hide_index=True,
        )
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

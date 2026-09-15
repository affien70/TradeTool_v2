from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st

from tradetool.config.runtime_settings import inspect_app_database
from tradetool.ui.screener import (
    build_incumbent_screener_ui_result,
    build_minimal_screener_result,
    build_selected_ticker_chart_detail,
    build_selected_ticker_detail,
    incumbent_screener_table_rows,
)

UNIVERSE_BENCHMARKS = {
    'NORWAY_V2': 'OSEBX.OL',
    'SP500': '^GSPC',
}
DATA_SOURCE_OPTIONS = ('yahoo',)
MISSING_DB_MESSAGE = 'Ingen lokal app-database funnet. Gå til Innstillinger eller bygg lokal database før screening.'


def _parse_ticker_text(value: str) -> list[str]:
    return [token.strip().upper() for token in value.replace(',', ' ').split() if token.strip()]


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


def render() -> None:
    st.title('Screener')
    st.write('Kjør den nåværende diagnostiske v2-screenerkjeden manuelt for å inspisere rangerte kandidater, trade signal og candidate type.')
    st.info('Dette er kun diagnostisk beslutningsstøtte. Resultatet er ikke produksjonsråd eller automatisk handelslogikk.')
    db_status = _show_database_status()

    st.header('Incumbent screener')
    st.caption('Baseline `incumbent_naive_rs_6m_top_10_v0`. Risikotagger er informasjon, ikke filtre eller rangering.')
    incumbent_columns = st.columns(5)
    incumbent_universe_id = incumbent_columns[0].selectbox('Universe', options=list(UNIVERSE_BENCHMARKS), index=0, key='incumbent_universe_id')
    incumbent_benchmark = UNIVERSE_BENCHMARKS[incumbent_universe_id]
    incumbent_columns[1].metric('Benchmark', incumbent_benchmark)
    incumbent_data_source = incumbent_columns[2].selectbox('Data source', options=list(DATA_SOURCE_OPTIONS), index=0, key='incumbent_data_source')
    incumbent_as_of_date = incumbent_columns[3].date_input('As-of date', value=date.today(), key='incumbent_as_of_date')
    incumbent_top_n = int(incumbent_columns[4].number_input('Top N', min_value=1, max_value=100, value=10, step=1, key='incumbent_top_n'))

    if st.button('Kjør incumbent screener'):
        if not _database_ready(db_status):
            st.warning(MISSING_DB_MESSAGE)
            return
        try:
            incumbent_result = build_incumbent_screener_ui_result(
                db_path=Path(db_status.configured_path),
                universe_id=incumbent_universe_id,
                benchmark_ticker=incumbent_benchmark,
                as_of_date=incumbent_as_of_date,
                data_source=incumbent_data_source,
                top_n=incumbent_top_n,
            )
            st.session_state['incumbent_screener_result'] = incumbent_result
        except Exception as exc:  # pragma: no cover
            st.error(str(exc))
            return

    incumbent_result = st.session_state.get('incumbent_screener_result')
    if incumbent_result is not None:
        st.subheader('Incumbent oppsummering')
        incumbent_summary = st.columns(5)
        incumbent_summary[0].metric('Univers', incumbent_result.universe_id)
        incumbent_summary[1].metric('Benchmark', incumbent_result.benchmark_ticker)
        incumbent_summary[2].metric('As-of', incumbent_result.as_of_date)
        incumbent_summary[3].metric('Eligible/rangert', incumbent_result.eligible_count)
        incumbent_summary[4].metric('Valgt', incumbent_result.selected_count)
        st.json(
            {
                'baseline_id': incumbent_result.baseline_id,
                'universe_source': incumbent_result.universe_source,
                'requested_stock_ticker_count': incumbent_result.requested_stock_ticker_count,
                'effective_feature_date': incumbent_result.effective_feature_date,
                'close_input_source': incumbent_result.close_input_source,
                'risk_tags_are_filters': False,
            }
        )
        st.dataframe(incumbent_screener_table_rows(incumbent_result), use_container_width=True)
        with st.expander('Avvisninger og datagap'):
            st.dataframe(list(incumbent_result.rejections), use_container_width=True)

    st.header('Diagnostisk screener')
    price_source_label = st.radio(
        'Prisdatasource',
        options=['Legacy price_history', 'V2 price_history_v2'],
        index=0,
        horizontal=True,
    )
    database_path = db_status.configured_path_text
    if price_source_label == 'V2 price_history_v2':
        st.warning('V2 price_history_v2 er kun test/diagnostisk markedsdata. UI-en kan ikke oppdatere eller skrive data.')
        universe_id = st.selectbox('Universe', options=list(UNIVERSE_BENCHMARKS), index=0, key='diagnostic_v2_universe_id')
        explicit_ticker_text = st.text_area('Tickere for v2-test', value='CAMBI.OL SNTIA.OL GOD.OL')
        benchmark_ticker = UNIVERSE_BENCHMARKS[universe_id]
        st.metric('Benchmark', benchmark_ticker)
        data_source = st.selectbox('Data source', options=list(DATA_SOURCE_OPTIONS), index=0, key='diagnostic_v2_data_source')
        price_table = 'price_history_v2'
    else:
        universe_id = st.selectbox('Universe', options=list(UNIVERSE_BENCHMARKS), index=0, key='diagnostic_universe_id')
        explicit_ticker_text = ''
        benchmark_ticker = UNIVERSE_BENCHMARKS[universe_id]
        st.metric('Benchmark', benchmark_ticker)
        data_source = st.selectbox('Data source', options=list(DATA_SOURCE_OPTIONS), index=0, key='diagnostic_data_source')
        price_table = 'price_history'
    show_avoid = st.checkbox('Vis AVOID-rader', value=False)

    if st.button('Kjør diagnostisk screener'):
        if not _database_ready(db_status):
            st.warning(MISSING_DB_MESSAGE)
            return
        explicit_tickers = _parse_ticker_text(explicit_ticker_text) if price_table == 'price_history_v2' else None
        if price_table == 'price_history_v2' and not explicit_tickers:
            st.warning('Oppgi minst én ticker for v2 price_history_v2-modus.')
            return

        try:
            result = build_minimal_screener_result(
                db_path=Path(database_path.strip()),
                universe_id=universe_id.strip() or 'NORWAY_V2',
                benchmark_ticker=benchmark_ticker.strip() or None,
                explicit_tickers=explicit_tickers,
                price_table=price_table,
                data_source=data_source,
            )
            st.session_state['screener_result'] = result
            st.session_state['screener_db_path'] = database_path
            st.session_state['screener_universe_id'] = universe_id
            st.session_state['screener_benchmark_ticker'] = benchmark_ticker
            st.session_state['screener_price_table'] = price_table
            st.session_state['screener_data_source'] = data_source
        except Exception as exc:  # pragma: no cover
            st.error(str(exc))
            return

    result = st.session_state.get('screener_result')
    if result is None:
        return

    current_db_path = st.session_state.get('screener_db_path', database_path.strip())
    current_benchmark = st.session_state.get('screener_benchmark_ticker', benchmark_ticker.strip() or None)
    current_price_table = st.session_state.get('screener_price_table', price_table)
    current_data_source = st.session_state.get('screener_data_source', data_source)

    st.subheader('Kjøreoppsummering')
    summary_columns = st.columns(5)
    summary_columns[0].metric('Input-univers', result.input_universe_count)
    summary_columns[1].metric('Strukturelt eligible', result.structural_eligible_count)
    summary_columns[2].metric('Strukturelt avvist', result.structural_rejected_count)
    summary_columns[3].metric('Feature-complete', result.feature_complete_count)
    summary_columns[4].metric('Rangert', result.ranked_count)

    st.json(
        {
            'universe_id': result.universe_id,
            'universe_source': result.universe_source,
            'benchmark_ticker': result.benchmark_ticker,
            'price_table': result.price_table,
            'data_source': result.data_source,
            'close_input_source': result.close_input_source,
            'benchmark_alignment_date': result.benchmark_alignment_date,
            'benchmark_lag_warning_count': result.benchmark_lag_warning_count,
            'ranking_engine_id': result.ranking_engine_id,
            'policy_engine_id': result.policy_engine_id,
            'classification_engine_id': result.classification_engine_id,
            'trade_signal_counts': dict(result.trade_signal_counts),
            'candidate_type_counts': dict(result.candidate_type_counts),
        }
    )

    st.subheader('Rangerte kandidater')
    st.caption('Standardvisningen skjuler AVOID-rader, men rå rank beholdes alltid som diagnostikk.')
    visible_rows = result.visible_rows(include_avoid=show_avoid)
    st.dataframe([row.to_dict() for row in visible_rows], use_container_width=True)

    st.subheader('Valgt ticker')
    if not visible_rows:
        st.warning('Ingen rader tilgjengelig med gjeldende filter.')
    else:
        default_ticker = visible_rows[0].ticker
        selected_ticker = st.selectbox(
            'Velg ticker for detaljvisning',
            options=[row.ticker for row in visible_rows],
            index=0,
        )
        detail = build_selected_ticker_detail(result, ticker=selected_ticker or default_ticker, include_avoid=show_avoid)
        chart_detail = st.cache_data(show_spinner=False)(build_selected_ticker_chart_detail)(
            db_path=Path(current_db_path),
            ticker=detail.ticker,
            benchmark_ticker=current_benchmark,
            price_table=current_price_table,
            data_source=current_data_source,
        )

        detail_columns = st.columns(4)
        detail_columns[0].metric('Ticker', detail.ticker)
        detail_columns[1].metric('Rå rank', detail.raw_rank)
        detail_columns[2].metric('Trade signal', detail.trade_signal)
        detail_columns[3].metric('Candidate type', detail.candidate_type)
        st.json(
            {
                'raw_score': detail.raw_score,
                'latest_close': detail.latest_close,
                'latest_price_date': detail.latest_price_date,
                'policy_reasons': list(detail.policy_reasons),
                'policy_warnings': list(detail.policy_warnings),
                'classification_reasons': list(detail.classification_reasons),
                'classification_warnings': list(detail.classification_warnings),
            }
        )

        st.subheader('Prischart')
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
                'height': 280,
            },
            use_container_width=True,
        )

        st.subheader('Benchmark og relativ styrke')
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
                        'height': 220,
                    },
                    {
                        'mark': {'type': 'line', 'color': '#f97316'},
                        'encoding': {
                            'x': {'field': 'price_date', 'type': 'temporal', 'title': 'Dato'},
                            'y': {'field': 'relative_strength_line', 'type': 'quantitative', 'title': 'RS-linje'},
                        },
                        'transform': [{'filter': 'isValid(datum.relative_strength_line)'}],
                        'height': 160,
                    },
                ]
            },
            use_container_width=True,
        )

        st.subheader('Diagnostiske felt')
        st.dataframe(
            [
                {'felt': key, 'verdi': value}
                for key, value in detail.metrics.items()
            ],
            use_container_width=True,
        )

    st.subheader('Avvisning og diagnose')
    st.dataframe(
        [{'reason': key, 'count': value} for key, value in sorted(result.structural_rejection_counts_by_reason.items())],
        use_container_width=True,
    )
    if result.feature_missing_reason_counts:
        st.dataframe(
            [{'reason': key, 'count': value} for key, value in sorted(result.feature_missing_reason_counts.items())],
            use_container_width=True,
        )
    st.dataframe([row.to_dict() for row in result.signal_type_matrix], use_container_width=True)


render()

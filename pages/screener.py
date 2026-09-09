from __future__ import annotations

from pathlib import Path

import streamlit as st

from tradetool.ui.screener import (
    build_minimal_screener_result,
    build_selected_ticker_chart_detail,
    build_selected_ticker_detail,
)


def _parse_ticker_text(value: str) -> list[str]:
    return [token.strip().upper() for token in value.replace(',', ' ').split() if token.strip()]


def render() -> None:
    st.title('Screener')
    st.write('Kjør den nåværende diagnostiske v2-screenerkjeden manuelt for å inspisere rangerte kandidater, trade signal og candidate type.')
    st.info('Dette er kun diagnostisk beslutningsstøtte. Resultatet er ikke produksjonsråd eller automatisk handelslogikk.')

    price_source_label = st.radio(
        'Prisdatasource',
        options=['Legacy price_history', 'V2 price_history_v2'],
        index=0,
        horizontal=True,
    )
    database_path = st.text_input('Lokal kopi av SQLite-database', value='', placeholder='/tmp/portfolio_copy.sqlite')
    if price_source_label == 'V2 price_history_v2':
        st.warning('V2 price_history_v2 er kun test/diagnostisk markedsdata. UI-en kan ikke oppdatere eller skrive data.')
        universe_id = st.text_input('Universe ID', value='EXPLICIT_V2_TEST')
        explicit_ticker_text = st.text_area('Tickere for v2-test', value='CAMBI.OL SNTIA.OL GOD.OL')
        benchmark_ticker = st.text_input('Benchmark ticker', value='OSEBX.OL')
        data_source = st.text_input('V2 data_source', value='yahoo')
        price_table = 'price_history_v2'
    else:
        universe_id = st.text_input('Universe ID', value='NORWAY_V2')
        explicit_ticker_text = ''
        benchmark_ticker = st.text_input('Benchmark ticker', value='^OSEAX')
        data_source = 'yahoo'
        price_table = 'price_history'
    show_avoid = st.checkbox('Vis AVOID-rader', value=False)

    if st.button('Kjør diagnostisk screener'):
        if not database_path.strip():
            st.warning('Oppgi en lokal databasebane før du kjører screeneren.')
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
                data_source=data_source.strip() or 'yahoo',
            )
            st.session_state['screener_result'] = result
            st.session_state['screener_db_path'] = database_path.strip()
            st.session_state['screener_universe_id'] = universe_id.strip() or 'NORWAY_V2'
            st.session_state['screener_benchmark_ticker'] = benchmark_ticker.strip() or None
            st.session_state['screener_price_table'] = price_table
            st.session_state['screener_data_source'] = data_source.strip() or 'yahoo'
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

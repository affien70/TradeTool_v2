from __future__ import annotations

from pathlib import Path

import streamlit as st

from tradetool.ui.screener import build_minimal_screener_result


def render() -> None:
    st.title('Screener')
    st.write('Kjør den nåværende diagnostiske v2-screenerkjeden manuelt for å inspisere rangerte kandidater, trade signal og candidate type.')
    st.info('Dette er kun diagnostisk beslutningsstøtte. Resultatet er ikke produksjonsråd eller automatisk handelslogikk.')

    database_path = st.text_input('Lokal kopi av SQLite-database', value='', placeholder='/tmp/portfolio_copy.sqlite')
    universe_id = st.text_input('Universe ID', value='NORWAY_V2')
    benchmark_ticker = st.text_input('Benchmark ticker', value='^OSEAX')
    show_avoid = st.checkbox('Vis AVOID-rader', value=False)

    if st.button('Kjør diagnostisk screener'):
        if not database_path.strip():
            st.warning('Oppgi en lokal databasebane før du kjører screeneren.')
            return

        try:
            result = build_minimal_screener_result(
                db_path=Path(database_path.strip()),
                universe_id=universe_id.strip() or 'NORWAY_V2',
                benchmark_ticker=benchmark_ticker.strip() or None,
            )
        except Exception as exc:  # pragma: no cover
            st.error(str(exc))
            return

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
                'ranking_engine_id': result.ranking_engine_id,
                'policy_engine_id': result.policy_engine_id,
                'classification_engine_id': result.classification_engine_id,
                'trade_signal_counts': dict(result.trade_signal_counts),
                'candidate_type_counts': dict(result.candidate_type_counts),
            }
        )

        st.subheader('Rangerte kandidater')
        st.caption('Standardvisningen skjuler AVOID-rader, men rå rank beholdes alltid som diagnostikk.')
        st.dataframe(
            [row.to_dict() for row in result.visible_rows(include_avoid=show_avoid)],
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

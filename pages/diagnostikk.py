from __future__ import annotations

from pathlib import Path

import streamlit as st

from tradetool.diagnostics.coverage import build_coverage_diagnostics
from tradetool.diagnostics.eligibility import build_eligibility_diagnostics


def _render_distribution_table(distribution: dict[str, int]) -> None:
    rows = [{'latest_data_date': key, 'ticker_count': value} for key, value in distribution.items()]
    if rows:
        st.dataframe(rows, use_container_width=True)


def render() -> None:
    st.title('Diagnostikk')
    st.write('Phase 3b gir kun manuell, lesebasert dekning- og structural-eligibility-diagnostikk.')
    st.info('Ingen produksjonsdatabase er koblet til. Oppgi kun en lokal kopi av SQLite-filen hvis du vil inspisere dekning eller structural eligibility.')

    database_path = st.text_input('Lokal kopi av SQLite-database', value='', placeholder='/tmp/portfolio_copy.sqlite')
    universe_id = st.text_input('Universe ID', value='NORWAY_V2')
    price_table = st.text_input('Pris-tabell (valgfritt)', value='')

    if st.button('Inspiser skjemadekning'):
        if not database_path.strip():
            st.warning('Oppgi en lokal databasebane for å kjøre lesetilgangsdiagnostikk.')
            return

        try:
            result = build_coverage_diagnostics(
                db_path=Path(database_path.strip()),
                universe_id=universe_id.strip() or 'UNSPECIFIED',
                price_table=price_table.strip() or None,
            )
        except Exception as exc:  # pragma: no cover - surfaced in UI only
            st.error(str(exc))
            return

        st.subheader('Dekningsrapport')
        st.json(result.to_dict())
        st.subheader('Siste datodispersjon')
        _render_distribution_table(dict(result.report.latest_data_date_distribution))

    if st.button('Kjør structural eligibility'):
        if not database_path.strip():
            st.warning('Oppgi en lokal databasebane for å kjøre structural-eligibility-diagnostikk.')
            return

        try:
            result = build_eligibility_diagnostics(
                db_path=Path(database_path.strip()),
                universe_id=universe_id.strip() or 'UNSPECIFIED',
                price_table=price_table.strip() or None,
            )
        except Exception as exc:  # pragma: no cover - surfaced in UI only
            st.error(str(exc))
            return

        st.subheader('Structural eligibility')
        st.caption('Dette er bare strukturell eligibility for fremtidig rangering, ikke en screener eller kjøpsliste.')
        st.json(result.to_summary_dict())
        st.dataframe(
            [{'reason': key, 'count': value} for key, value in sorted(result.rejection_counts_by_reason.items())],
            use_container_width=True,
        )


render()

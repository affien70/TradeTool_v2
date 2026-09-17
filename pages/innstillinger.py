from __future__ import annotations

import streamlit as st

from tradetool.config.runtime_settings import inspect_app_database
from tradetool.data.app_market_data_update import (
    UNIVERSE_BENCHMARKS,
    run_app_market_data_update,
    validate_app_update_target,
    write_app_market_data_update_report,
)


def _render_database_status(status) -> None:
    st.header('Database')
    st.caption(f'Lokal app-database: {status.configured_path_text}')
    columns = st.columns(4)
    columns[0].metric('Finnes', 'Ja' if status.exists else 'Nei')
    columns[1].metric('Lesbar', 'Ja' if status.readable else 'Nei')
    columns[2].metric('price_history_v2', 'Ja' if status.price_history_v2_table_exists else 'Nei')
    columns[3].metric('Rader', status.row_count if status.row_count is not None else '-')
    st.caption(f'Siste prisdato: {status.latest_price_date or "Ukjent"}')
    if status.error:
        st.warning(f'Databasefeil: {status.error}')
    st.write('DB-filer er lokale og skal ikke committes til Git.')
    with st.expander('Tekniske databasedetaljer'):
        st.json({
            'configured_db_path': status.configured_path_text,
            'environment_variable': status.env_var_name,
            'environment_override_active': status.env_override_active,
            'exists': status.exists,
            'readable': status.readable,
            'price_history_v2_table_exists': status.price_history_v2_table_exists,
            'row_count': status.row_count,
            'latest_price_date': status.latest_price_date,
            'error': status.error,
        })


def _render_update_result(result) -> None:
    preview = result.mode == 'dry-run'
    st.subheader('Resultat av sjekk' if preview else 'Resultat av oppdatering')
    st.caption(f'Status: {result.mode} ({result.outcome}) | Mål: {result.target_db_path}')
    columns = st.columns(4)
    columns[0].metric('Forespurte tickere', len(result.requested_tickers))
    columns[1].metric('Hentede tickere', len(result.fetched_tickers))
    columns[2].metric('Manglende tickere', len(result.missing_tickers))
    columns[3].metric('Ugyldige rader', result.invalid_rows)
    columns = st.columns(3)
    columns[0].metric('Nye rader', result.would_insert if preview else result.inserted_rows)
    columns[1].metric('Oppdaterte rader', result.would_update if preview else result.updated_rows)
    columns[2].metric('Uendrede rader', result.would_skip if preview else result.skipped_rows)
    if not preview:
        st.caption(
            f'Ugyldige rader hoppet over: {result.invalid_rows_skipped}. '
            f'Totalt antall rader: {result.row_count}. Siste prisdato: {result.latest_price_date or "Ukjent"}.'
        )
    if result.missing_tickers:
        st.warning(f'Manglende tickere: {", ".join(result.missing_tickers)}')
    if result.tolerated_warnings:
        st.caption(f'Tolererte datavarsler: {dict(result.tolerated_warnings)}')
    if result.source_warnings:
        st.caption(f'Kildevarsler: {dict(result.source_warnings)}')
    if result.outcome == 'blocked_invalid_rows':
        st.warning('Ingen rader ble skrevet fordi ugyldige rader ble funnet.')


def _matches_current_database(result, *, universe_id: str, db_path) -> bool:
    return bool(result and result.universe_id == universe_id and result.target_db_path == db_path)


def render() -> None:
    st.title('Innstillinger')
    status = inspect_app_database()
    _render_database_status(status)
    report_warning = st.session_state.pop('app_update_report_warning', None)
    if report_warning:
        st.warning(report_warning)

    st.header('Oppdater lokal markedsdatabase')
    st.caption(
        'Henter gratis Yahoo-markedsdata til den lokale app-databasen. Dette kan ta tid. '
        'Screener bruker siste fullførte lokale oppdatering. Ingen automatisk handel eller anbefaling utføres.'
    )
    universe_id = st.selectbox('Univers', tuple(UNIVERSE_BENCHMARKS), key='app_update_universe')
    st.caption(f'Benchmark: {UNIVERSE_BENCHMARKS[universe_id]}')
    try:
        validate_app_update_target(status.configured_path, env_override_active=status.env_override_active)
        target_safe = True
    except ValueError as exc:
        target_safe = False
        st.error(f'Oppdatering er deaktivert for denne databasebanen: {exc}')

    if st.button('Sjekk oppdatering uten å skrive', disabled=not target_safe, key='app_update_dry_run'):
        st.session_state.pop('app_update_dry_result', None)
        try:
            with st.spinner('Henter markedsdata og kontrollerer mulige endringer...'):
                result = run_app_market_data_update(universe_id=universe_id)
        except Exception as exc:
            st.error(f'Kunne ikke sjekke oppdateringen: {exc}')
        else:
            st.session_state['app_update_dry_result'] = result
            st.session_state.pop('app_update_write_result', None)
            try:
                write_app_market_data_update_report(result)
            except OSError as exc:
                st.warning(f'Sjekken er fullført, men rapporten kunne ikke lagres: {exc}')

    dry_result = st.session_state.get('app_update_dry_result')
    dry_run_ready = _matches_current_database(dry_result, universe_id=universe_id, db_path=status.configured_path)
    if dry_run_ready:
        _render_update_result(dry_result)
    else:
        st.info('Kjør en sjekk for dette universet før du oppdaterer databasen.')

    confirmed = st.checkbox('Jeg forstår at dette oppdaterer lokal app-database', key='app_update_confirm')
    skip_invalid = st.checkbox(
        'Tillat å hoppe over ugyldige rader',
        disabled=not confirmed,
        key='app_update_skip_invalid',
    )
    can_write = target_safe and confirmed and dry_run_ready and (dry_result.invalid_rows == 0 or skip_invalid)
    if dry_run_ready and dry_result.invalid_rows and not skip_invalid:
        st.caption('Sjekken fant ugyldige rader. Bekreft separat hvis gyldige rader skal skrives likevel.')
    if st.button('Oppdater lokal app-database', disabled=not can_write, key='app_update_write'):
        if not can_write:
            st.warning('Sjekk og bekreft oppdateringen først.')
            return
        try:
            with st.spinner('Oppdaterer lokal app-database...'):
                result = run_app_market_data_update(
                    universe_id=universe_id,
                    allow_app_db_write=True,
                    allow_partial_invalid_skip=skip_invalid,
                )
        except Exception as exc:
            st.error(f'Kunne ikke oppdatere databasen: {exc}')
        else:
            st.session_state['app_update_write_result'] = result
            st.session_state.pop('app_update_dry_result', None)
            try:
                write_app_market_data_update_report(result)
            except OSError as exc:
                st.session_state['app_update_report_warning'] = (
                    f'Databasen er oppdatert, men rapporten kunne ikke lagres: {exc}'
                )
            st.rerun()

    write_result = st.session_state.get('app_update_write_result')
    if _matches_current_database(write_result, universe_id=universe_id, db_path=status.configured_path):
        _render_update_result(write_result)


render()

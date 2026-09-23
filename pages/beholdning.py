from __future__ import annotations

import sqlite3

import streamlit as st

from tradetool.config.runtime_settings import inspect_app_database
from tradetool.holdings import (
    HoldingSettings,
    build_holdings_page_result,
    dry_run_nordnet_import,
    import_nordnet_transactions,
    initialize_holdings_schema,
    save_holding_settings,
)
from tradetool.ui.holdings import (
    build_holding_chart_figure,
    build_holdings_detail_display,
    build_holdings_summary_display,
    build_holdings_table_rows,
    holding_position_options,
    page_state_message,
)


def _load_page_result(database_status):
    if not database_status.exists or not database_status.readable:
        st.warning('Lokal app-database er ikke tilgjengelig for lesing.')
        return None
    try:
        with sqlite3.connect(f'file:{database_status.configured_path}?mode=ro', uri=True) as connection:
            connection.row_factory = sqlite3.Row
            return build_holdings_page_result(
                holdings_connection=connection,
                market_data_db_path=database_status.configured_path,
            )
    except (OSError, sqlite3.Error, ValueError):
        st.warning('Beholdninger kan ikke leses akkurat nå.')
        return None


def _render_schema_onboarding(database_status) -> None:
    st.info('Beholdningslageret er ikke klargjort ennå. Vanlig sidevisning oppretter ikke tabeller.')
    can_initialize = database_status.exists and database_status.readable
    if st.button('Klargjør beholdningslager', disabled=not can_initialize, key='holdings_initialize_schema'):
        try:
            with sqlite3.connect(database_status.configured_path) as connection:
                initialize_holdings_schema(connection)
        except (OSError, sqlite3.Error, ValueError):
            st.error('Kunne ikke klargjøre beholdningslageret.')
        else:
            st.success('Beholdningslageret er klargjort.')
            st.rerun()


def _render_import_preview(preview) -> None:
    st.caption('Forhåndsvisning fra importmotoren. Ingen transaksjoner er skrevet.')
    first_row = st.columns(5)
    first_row[0].metric('Kilderader', preview.parse_result.source_row_count)
    first_row[1].metric('Nye transaksjoner', preview.new_row_count)
    first_row[2].metric('Allerede registrert', preview.existing_idempotent_count)
    first_row[3].metric('Oppdateres', preview.would_update_count)
    first_row[4].metric('Duplikater i fil', preview.duplicate_upload_count)
    second_row = st.columns(5)
    second_row[0].metric('Ignorerte kontantbevegelser', preview.ignored_non_holdings_cashflow_count)
    second_row[1].metric('Ignorerte administrative', preview.ignored_administrative_count)
    second_row[2].metric('Ugyldige rader', preview.invalid_row_count)
    second_row[3].metric('Ikke støttede rader', preview.unsupported_blocker_count)
    second_row[4].metric('Konflikter', preview.conflict_count)


def _render_import_receipt() -> None:
    receipt = st.session_state.pop('holdings_import_receipt', None)
    if receipt is None:
        return
    st.success(
        f'Import fullført: {receipt["written"]} skrevet, {receipt["updated"]} oppdatert, '
        f'{receipt["existing"]} allerede registrert, {receipt["ignored"]} ignorert.'
    )


def _render_nordnet_import(database_status) -> None:
    st.subheader('Importer Nordnet-transaksjoner')
    _render_import_receipt()
    uploaded_file = st.file_uploader(
        'Last opp Nordnet-transaksjonseksport',
        type=('csv', 'txt'),
        key='holdings_nordnet_upload',
    )
    if uploaded_file is None:
        st.caption('Last opp en Nordnet-fil for å se en forhåndsvisning før import.')
        return
    try:
        content = uploaded_file.getvalue()
        with sqlite3.connect(f'file:{database_status.configured_path}?mode=ro', uri=True) as connection:
            connection.row_factory = sqlite3.Row
            preview = dry_run_nordnet_import(content, target_connection=connection)
    except (OSError, sqlite3.Error, ValueError):
        st.error('Kunne ikke kontrollere Nordnet-filen.')
        return

    _render_import_preview(preview)
    meaningful_write = preview.new_row_count + preview.would_update_count > 0
    can_import = preview.target_schema_ready and not preview.has_blocking_errors and meaningful_write
    if preview.has_blocking_errors:
        st.warning('Import er blokkert fordi filen inneholder ugyldige, ikke støttede eller konfliktfylte rader.')
    elif not meaningful_write:
        st.info('Filen inneholder ingen nye eller oppdaterte beholdningstransaksjoner.')
    if st.button('Importer transaksjoner', disabled=not can_import, key='holdings_confirm_nordnet_import'):
        try:
            with sqlite3.connect(database_status.configured_path) as connection:
                result = import_nordnet_transactions(content, target_connection=connection, confirmed=True)
        except (OSError, sqlite3.Error, ValueError):
            st.error('Kunne ikke importere Nordnet-transaksjonene.')
            return
        st.session_state['holdings_import_receipt'] = {
            'written': result.written_row_count,
            'updated': result.preview.would_update_count,
            'existing': result.preview.existing_idempotent_count,
            'ignored': (
                result.preview.ignored_non_holdings_cashflow_count
                + result.preview.ignored_administrative_count
            ),
        }
        st.rerun()


def _render_holdings_settings(database_status, settings: HoldingSettings) -> None:
    st.subheader('Beholdningsinnstillinger')
    st.caption(f'Benchmark: {settings.norway_benchmark_id}')
    period_label = st.selectbox(
        'Analyseperiode',
        options=('1 år', '2 år', '5 år'),
        index=('1 år', '2 år', '5 år').index(settings.period_label),
        key='holdings_period_label',
    )
    rs_months = st.selectbox(
        'RS-periode',
        options=(3, 6, 12),
        index=(3, 6, 12).index(settings.rs_months),
        key='holdings_rs_months',
    )
    sell_fast_sma_days = st.number_input(
        'Rask SMA for salg',
        min_value=0,
        step=1,
        value=settings.sell_fast_sma_days,
        key='holdings_sell_fast_sma_days',
    )
    sell_rs_weak = st.checkbox('Bruk svak RS i salgssignal', value=settings.sell_rs_weak, key='holdings_sell_rs_weak')
    sell_below_cost_basis = st.checkbox(
        'Bruk kostpris-stopp',
        value=settings.sell_below_cost_basis,
        key='holdings_sell_below_cost_basis',
    )
    sell_drop_from_peak = st.checkbox(
        'Bruk ATR-trailing-stopp',
        value=settings.sell_drop_from_peak,
        key='holdings_sell_drop_from_peak',
    )
    updated_settings = HoldingSettings(
        period_label=period_label,
        rs_months=rs_months,
        sell_rs_weak=sell_rs_weak,
        sell_below_cost_basis=sell_below_cost_basis,
        sell_drop_from_peak=sell_drop_from_peak,
        sell_fast_sma_days=int(sell_fast_sma_days),
        atr_multiplier=settings.atr_multiplier,
        rs_threshold=settings.rs_threshold,
        norway_benchmark_id=settings.norway_benchmark_id,
    )
    if st.button('Lagre innstillinger', key='holdings_save_settings'):
        try:
            with sqlite3.connect(database_status.configured_path) as connection:
                save_holding_settings(connection, updated_settings)
        except (OSError, sqlite3.Error, ValueError):
            st.error('Kunne ikke lagre beholdningsinnstillingene.')
        else:
            st.success('Beholdningsinnstillingene er lagret.')
            st.rerun()


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
    database_status = inspect_app_database()
    result = _load_page_result(database_status)
    if result is None:
        return

    if not result.holdings_schema_ready:
        _render_schema_onboarding(database_status)
        return

    if result.rows:
        _render_summary(result)
        _render_active_holdings(result)
        _render_selected_position(result)
    else:
        state_message = page_state_message(result)
        if state_message:
            st.info(state_message)
    _render_nordnet_import(database_status)
    _render_holdings_settings(database_status, result.settings)


render()

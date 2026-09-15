from __future__ import annotations

import streamlit as st

from tradetool.config.runtime_settings import inspect_app_database


def render() -> None:
    st.title('Innstillinger')
    st.write('Dette er Phase 2-skallet for applikasjonsinnstillinger i TradeTool v2.')
    st.info('Bare grunnkonfigurasjon er definert; ingen produksjonsinnstillinger er aktivert ennå.')

    status = inspect_app_database()
    st.header('Database')
    st.write('DB-filer er lokale og skal ikke committes til Git.')
    st.json(
        {
            'configured_db_path': status.configured_path_text,
            'environment_variable': status.env_var_name,
            'environment_override_active': status.env_override_active,
            'exists': status.exists,
            'readable': status.readable,
            'price_history_v2_table_exists': status.price_history_v2_table_exists,
            'row_count': status.row_count,
            'latest_price_date': status.latest_price_date,
            'error': status.error,
        }
    )


render()

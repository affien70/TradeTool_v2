from __future__ import annotations

import streamlit as st


def render() -> None:
    st.title('Screener')
    st.write('Dette er Phase 2-skallet for den fremtidige screener-modulen i TradeTool v2.')
    st.info('Ingen rangering, policy eller markedsdata er koblet til ennå.')


render()

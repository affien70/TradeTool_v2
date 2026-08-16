from __future__ import annotations

import streamlit as st


def render() -> None:
    st.title('Beholdning')
    st.write('Dette er Phase 2-skallet for beholdningsmodulen i TradeTool v2.')
    st.info('Ingen beholdningslogikk eller databasestrenger er implementert ennå.')


render()

from __future__ import annotations

import streamlit as st


def render() -> None:
    st.title('Diagnostikk')
    st.write('Dette er Phase 2-skallet for diagnostikk og evidens i TradeTool v2.')
    st.info('Ingen artefakter, rapporter eller analyser lastes i denne fasen.')


render()

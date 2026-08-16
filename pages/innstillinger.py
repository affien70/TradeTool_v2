from __future__ import annotations

import streamlit as st


def render() -> None:
    st.title('Innstillinger')
    st.write('Dette er Phase 2-skallet for applikasjonsinnstillinger i TradeTool v2.')
    st.info('Bare grunnkonfigurasjon er definert; ingen produksjonsinnstillinger er aktivert ennå.')


render()

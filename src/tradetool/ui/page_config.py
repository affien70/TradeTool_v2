from __future__ import annotations

import streamlit as st

from tradetool.config.settings import AppSettings


def build_navigation(settings: AppSettings) -> st.navigation:
    pages = [
        st.Page('pages/screener.py', title='Screener', icon=':material/show_chart:', default=True),
        st.Page('pages/beholdning.py', title='Beholdning', icon=':material/account_balance_wallet:'),
        st.Page('pages/diagnostikk.py', title='Diagnostikk', icon=':material/monitoring:'),
        st.Page('pages/innstillinger.py', title='Innstillinger', icon=':material/settings:'),
    ]
    return st.navigation(pages, position='sidebar')

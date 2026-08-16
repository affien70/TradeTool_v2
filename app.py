from __future__ import annotations

import streamlit as st

from tradetool.config.settings import get_settings
from tradetool.ui.page_config import build_navigation


def main() -> None:
    settings = get_settings()
    st.set_page_config(
        page_title=settings.application_name,
        page_icon=':material/show_chart:',
        layout='wide',
    )
    navigation = build_navigation(settings)
    navigation.run()


if __name__ == '__main__':
    main()

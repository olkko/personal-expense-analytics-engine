"""Streamlit page for bank authorization and transaction import."""

import os

import streamlit as st

from personal_expense_analytics_engine._dashboard import (
    DEFAULT_DATABASE,
    _authorization_tab,
)

st.set_page_config(page_title="Import account", page_icon="🏦")

database = st.sidebar.text_input(
    "SQLite database",
    os.environ.get("EXPENSES_DB", DEFAULT_DATABASE),
)
st.title("Import bank transactions")
_authorization_tab(database)

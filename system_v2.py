import streamlit as st

from finance_app.ui import run_app


if __name__ == "__main__":
    st.set_page_config(page_title="Personal Finance Manager", layout="wide")
    run_app()

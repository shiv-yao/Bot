import streamlit as st
import requests

API = "http://localhost:8000"

st.set_page_config(layout="wide")

st.title("🔥 Quant Fund Dashboard")

metrics = requests.get(f"{API}/metrics").json()
state = requests.get(f"{API}/").json()

col1, col2 = st.columns(2)

col1.metric("Capital", round(state["capital"], 2))
col2.metric("Positions", state["positions"])

st.subheader("📊 Performance")

if metrics:
    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Win Rate", metrics["win_rate"])
    col2.metric("Profit Factor", metrics["profit_factor"])
    col3.metric("Drawdown", metrics["max_drawdown"])
    col4.metric("Sharpe", metrics["sharpe"])

    st.subheader("Equity Curve")
    st.line_chart(metrics["equity_curve"])

st.subheader("Logs")
logs = requests.get(f"{API}/logs").json()

for l in reversed(logs):
    st.text(l)

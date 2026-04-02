import streamlit as st
import requests

API = "http://localhost:8000"

st.set_page_config(page_title="Trading Dashboard", layout="wide")

st.title("🔥 Quant Fund Dashboard")

metrics = requests.get(f"{API}/metrics").json()
state = requests.get(f"{API}/state").json()

col1, col2, col3 = st.columns(3)

col1.metric("Capital", round(state["capital"], 2))
col2.metric("Win Streak", state["win_streak"])
col3.metric("Loss Streak", state["loss_streak"])

st.subheader("Performance")

st.write(metrics)

st.subheader("Equity Curve")

if metrics:
    st.line_chart(metrics["equity_curve"])

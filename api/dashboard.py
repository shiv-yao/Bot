import streamlit as st
import requests
import pandas as pd
import plotly.express as px

# 👉 改成你的 API
API_URL = "https://你的-railway-url/metrics"

st.set_page_config(layout="wide")

st.title("🚀 Trading AI Dashboard (Fund Level)")

# ================= FETCH =================

def fetch():
    try:
        return requests.get(API_URL).json()
    except:
        return None

data = fetch()

if not data:
    st.error("API 無法連線")
    st.stop()

# ================= SUMMARY =================

col1, col2, col3, col4 = st.columns(4)

col1.metric("PnL", round(data["realized_pnl"], 4))
col2.metric("Positions", len(data["positions"]))
col3.metric("Errors", data["errors"])
col4.metric("Version", data["bot_version"])

# ================= POSITIONS =================

st.subheader("📌 Open Positions")

if data["positions"]:
    df = pd.DataFrame(data["positions"])
    st.dataframe(df, use_container_width=True)
else:
    st.info("No positions")

# ================= CLOSED =================

st.subheader("💰 Closed Trades")

if "closed_trades" in data and data["closed_trades"]:
    df = pd.DataFrame(data["closed_trades"])

    st.dataframe(df, use_container_width=True)

    if "pnl" in df:
        fig = px.histogram(df, x="pnl", nbins=30, title="PnL Distribution")
        st.plotly_chart(fig, use_container_width=True)

# ================= EQUITY =================

st.subheader("📈 Equity Curve")

if "closed_trades" in data and data["closed_trades"]:
    pnl_series = [t["pnl"] for t in data["closed_trades"] if "pnl" in t]

    if pnl_series:
        equity = pd.Series(pnl_series).cumsum()

        fig = px.line(equity, title="Equity Curve")
        st.plotly_chart(fig, use_container_width=True)

# ================= RISK =================

st.subheader("⚠️ Risk Monitor")

if data["loss_streak"] >= 3:
    st.warning(f"⚠️ Loss streak: {data['loss_streak']}")

if data["loss_streak"] >= 5:
    st.error("🚨 Kill switch zone")

# ================= RAW =================

with st.expander("🔍 Raw JSON"):
    st.json(data)

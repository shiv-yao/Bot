import os
import time
import requests
import streamlit as st
import pandas as pd

API = os.getenv("API_BASE_URL", "http://localhost:8000")

st.set_page_config(layout="wide")

st.title("🔥 Quant Trading Dashboard")

# ===== AUTO REFRESH =====
REFRESH = st.sidebar.slider("Refresh (sec)", 1, 10, 2)

while True:

    try:
        metrics = requests.get(f"{API}/metrics", timeout=5).json()

        summary = metrics["summary"]
        perf = metrics["performance"]

        # ===== HEADER =====
        c1, c2, c3, c4 = st.columns(4)

        c1.metric("💰 Capital", summary["capital"])
        c2.metric("📈 Return %", summary["return_pct"])
        c3.metric("📉 Drawdown", summary["drawdown"])
        c4.metric("⚡ Trades", perf["trades"])

        # ===== PERFORMANCE =====
        st.subheader("📊 Performance")

        c1, c2, c3, c4 = st.columns(4)

        c1.metric("Win Rate", perf["win_rate"])
        c2.metric("Profit Factor", perf["profit_factor"])
        c3.metric("Sharpe", perf["sharpe"])
        c4.metric("Max DD", perf["max_drawdown"])

        # ===== EQUITY =====
        st.subheader("📈 Equity Curve")

        eq = metrics.get("equity_curve", [])
        if eq:
            df_eq = pd.DataFrame({"equity": eq})
            st.line_chart(df_eq)

        # ===== POSITIONS =====
        st.subheader("📦 Positions")

        pos = metrics.get("positions", [])
        if pos:
            df_pos = pd.DataFrame(pos)
            st.dataframe(df_pos, use_container_width=True)
        else:
            st.info("No open positions")

        # ===== TRADES =====
        st.subheader("🧾 Recent Trades")

        trades = metrics.get("recent_trades", [])
        if trades:
            df_trades = pd.DataFrame(trades)
            st.dataframe(df_trades, use_container_width=True)

        # ===== LOGS =====
        st.subheader("📜 Logs")

        logs = metrics.get("logs", [])
        for l in reversed(logs):
            st.text(l)

    except Exception as e:
        st.error(f"API Error: {e}")

    time.sleep(REFRESH)
    st.rerun()

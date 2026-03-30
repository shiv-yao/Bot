import streamlit as st, requests, os
API = os.getenv("API_BASE_URL", "http://localhost:8000")
st.title("Integrated Final Dashboard")
try:
    metrics = requests.get(f"{API}/metrics", timeout=10).json()
    st.metric("Capital", f"{metrics['capital']:.4f}")
    st.metric("Regime", metrics["regime"])
    st.metric("Threshold", f"{metrics['threshold']:.4f}")
    st.write("Wallets", metrics["wallets"])
    st.write("Stats", metrics["stats"])
    st.write("Recent Trades", metrics["recent_trades"])
    st.write("Recent Logs", metrics["recent_logs"])
except Exception as e:
    st.error(f"Dashboard fetch failed: {e}")

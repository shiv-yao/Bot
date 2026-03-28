import os
import time
import requests
import pandas as pd
import streamlit as st

API_BASE = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")

st.set_page_config(
    page_title="V90 Fund System",
    layout="wide",
)

st.title("💀 V90 Fund System")
st.caption("Fund-style control panel for your FastAPI trading backend")

def fetch_json(path: str):
    url = f"{API_BASE}{path}"
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        return r.json(), None
    except Exception as e:
        return None, str(e)

with st.sidebar:
    st.header("Connection")
    st.write(f"API: `{API_BASE}`")
    refresh_sec = st.slider("Refresh (sec)", min_value=1, max_value=30, value=3)

placeholder = st.empty()

while True:
    metrics, metrics_err = fetch_json("/metrics")
    brain, brain_err = fetch_json("/brain")

    with placeholder.container():
        if metrics_err or brain_err:
            st.error(f"API error: {metrics_err or brain_err}")
        else:
            positions = metrics.get("positions", [])
            closed = metrics.get("closed", [])
            alpha_scores = metrics.get("alpha_scores", [])
            allocator = metrics.get("allocator", {})
            alpha_models = brain.get("alpha_models", {})
            daily_pnl = metrics.get("daily_pnl", 0)
            loss_streak = metrics.get("loss_streak", 0)
            daily_trades = metrics.get("daily_trades", 0)
            last_error = metrics.get("last_error")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Daily PnL", f"{daily_pnl:.4f}")
            c2.metric("Open Positions", len(positions))
            c3.metric("Daily Trades", daily_trades)
            c4.metric("Loss Streak", loss_streak)

            left, right = st.columns([2, 1])

            with left:
                st.subheader("Alpha Curve")
                if alpha_scores:
                    df_alpha = pd.DataFrame({"alpha": alpha_scores})
                    st.line_chart(df_alpha, height=260)
                else:
                    st.info("No alpha data yet.")

            with right:
                st.subheader("Allocator")
                if allocator:
                    df_alloc = pd.DataFrame(
                        {"source": list(allocator.keys()), "weight": list(allocator.values())}
                    ).set_index("source")
                    st.bar_chart(df_alloc, height=260)
                else:
                    st.info("No allocator data yet.")

            st.subheader("Alpha Models")
            if alpha_models:
                rows = []
                for name, model in alpha_models.items():
                    hist = model.get("history", [])
                    rows.append({
                        "model": name,
                        "score": model.get("score", 0),
                        "samples": len(hist),
                        "avg_pnl": (sum(hist) / len(hist)) if hist else 0,
                        "winrate": (sum(1 for x in hist if x > 0) / len(hist)) if hist else 0,
                    })
                df_models = pd.DataFrame(rows).set_index("model")
                st.dataframe(df_models, use_container_width=True)
            else:
                st.info("No alpha model data yet.")

            col_a, col_b = st.columns(2)

            with col_a:
                st.subheader("Open Positions")
                if positions:
                    df_pos = pd.DataFrame(positions)
                    st.dataframe(df_pos, use_container_width=True, height=320)
                else:
                    st.info("No open positions.")

            with col_b:
                st.subheader("Closed Positions")
                if closed:
                    df_closed = pd.DataFrame(closed[-30:])
                    st.dataframe(df_closed, use_container_width=True, height=320)
                else:
                    st.info("No closed positions yet.")

            st.subheader("System Status")
            st.json({
                "last_error": last_error,
                "api_base": API_BASE,
                "positions": len(positions),
                "closed_count": len(closed),
            })

    time.sleep(refresh_sec)

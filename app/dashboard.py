import os
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Semi-live Jupiter Dashboard", layout="wide")
st.title("Semi-live Jupiter Dashboard")

api_base = st.sidebar.text_input(
    "API Base",
    value=os.getenv("RAILWAY_PUBLIC_API_BASE", "http://127.0.0.1:8000")
)
query = st.sidebar.text_input("Search token", value="SOL")
taker = st.sidebar.text_input("Taker public key", value="")
ui_amount = st.sidebar.number_input("Buy amount (SOL)", min_value=0.001, value=0.01, step=0.001)
slippage_bps = st.sidebar.number_input("Slippage (bps)", min_value=1, value=300, step=10)
confirm = st.sidebar.checkbox("I want to create an order", value=False)

def fetch_tokens():
    r = requests.get(f"{api_base}/tokens", params={"query": query}, timeout=30)
    r.raise_for_status()
    return r.json()["items"]

try:
    rows = fetch_tokens()
    df = pd.DataFrame(rows)
    preferred = [c for c in ["symbol","name","id","usdPrice","holderCount","mcap","organicScore","organicScoreLabel"] if c in df.columns]
    st.dataframe(df[preferred], use_container_width=True, hide_index=True)

    if not df.empty:
        selected_symbol = st.selectbox("Select token", df["symbol"].fillna(df["id"]).tolist())
        selected = df[df["symbol"].fillna(df["id"]) == selected_symbol].iloc[0]
        st.json({
            "id": selected.get("id"),
            "name": selected.get("name"),
            "symbol": selected.get("symbol"),
            "usdPrice": selected.get("usdPrice"),
            "holderCount": selected.get("holderCount"),
            "mcap": selected.get("mcap"),
            "organicScore": selected.get("organicScore"),
            "organicScoreLabel": selected.get("organicScoreLabel"),
        })

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Buy", use_container_width=True):
                payload = {
                    "taker": taker,
                    "outputMint": selected["id"],
                    "uiAmount": float(ui_amount),
                    "slippageBps": int(slippage_bps),
                    "confirm": bool(confirm),
                }
                r = requests.post(f"{api_base}/trade/buy", json=payload, timeout=60)
                st.write(r.status_code)
                st.json(r.json())
        with col2:
            sell_amount = st.number_input("Sell amount (raw units in this scaffold)", min_value=1.0, value=1000.0, step=1.0)
            if st.button("Sell", use_container_width=True):
                payload = {
                    "taker": taker,
                    "inputMint": selected["id"],
                    "uiAmount": float(sell_amount),
                    "slippageBps": int(slippage_bps),
                    "confirm": bool(confirm),
                }
                r = requests.post(f"{api_base}/trade/sell", json=payload, timeout=60)
                st.write(r.status_code)
                st.json(r.json())
except Exception as e:
    st.error(str(e))

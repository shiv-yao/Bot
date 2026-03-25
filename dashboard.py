import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Trading Insights", layout="wide")
st.title("📈 Trading Insights Dashboard")

API = "http://127.0.0.1:8000/report"

try:
    report = requests.get(API, timeout=5).json()
except Exception:
    st.error("API 尚未啟動，請確認 Replit 正在執行 start.py")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("總交易", report["total_trades"])
c2.metric("勝率", f"{report['win_rate']:.1%}")
c3.metric("平均 ROI", f"{report['avg_roi']:.2f}")
c4.metric("最大回撤", f"{report['max_drawdown']:.1%}")

st.subheader("資金曲線")
eq = pd.DataFrame(report["equity_curve"])
if not eq.empty:
    eq["timestamp"] = pd.to_datetime(eq["timestamp"])
    eq = eq.set_index("timestamp")
    st.line_chart(eq["equity"])
else:
    st.info("尚無交易資料")

col1, col2 = st.columns(2)
with col1:
    st.subheader("問題分類")
    issue_df = pd.DataFrame(list(report["issue_breakdown"].items()), columns=["issue","count"])
    st.bar_chart(issue_df.set_index("issue"))
with col2:
    st.subheader("每日 ROI")
    daily = pd.DataFrame(report["daily_roi"])
    if not daily.empty:
        daily["timestamp"] = pd.to_datetime(daily["timestamp"])
        daily = daily.set_index("timestamp")
        st.bar_chart(daily["roi"])

st.subheader("Smart Money Ranking")
sm = pd.DataFrame(report["smart_money"])
if not sm.empty:
    st.dataframe(sm, use_container_width=True)
else:
    st.info("沒有 wallet 排名資料")

st.subheader("策略分解")
strat = pd.DataFrame(report["strategy_breakdown"])
if not strat.empty:
    st.dataframe(strat, use_container_width=True)
else:
    st.info("沒有 strategy 資料")

st.subheader("建議參數")
st.json(report["recommended_config"])

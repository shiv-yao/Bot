from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.core.state import engine
from app.core.engine import main_loop
from app.alpha.wallet_alpha_v7 import wallet_trades, token_wallets

import asyncio

app = FastAPI()


# =============================
# 🚀 啟動引擎
# =============================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(main_loop())


# =============================
# ❤️ health（Railway用）
# =============================
@app.get("/health")
def health():
    return {"status": "ok"}


# =============================
# 💰 系統狀態
# =============================
@app.get("/status")
def status():
    return {
        "capital": engine.capital,
        "positions": len(engine.positions),
        "wins": engine.stats.get("wins", 0),
        "losses": engine.stats.get("losses", 0),
        "running": engine.running,
        "regime": getattr(engine, "regime", "unknown"),
    }


# =============================
# 📊 持倉
# =============================
@app.get("/positions")
def positions():
    return engine.positions


# =============================
# 📈 交易紀錄
# =============================
@app.get("/trades")
def trades():
    return engine.trade_history[-50:]


# =============================
# 🧠 Wallet Alpha（🔥重點）
# =============================
@app.get("/wallets")
def wallets():
    return {
        "tracked_wallets": len(wallet_trades),
        "wallet_trades": dict(list(wallet_trades.items())[:20]),
    }


@app.get("/token-wallets")
def token_wallet_view():
    return dict(list(token_wallets.items())[:20])


# =============================
# 🔍 Debug（看 logs）
# =============================
@app.get("/logs")
def logs():
    return getattr(engine, "logs", [])[-50:]


# =============================
# 🧪 Web UI（簡單版）
# =============================
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return f"""
    <html>
    <body style="font-family: monospace;">
        <h2>🔥 AI Trading System</h2>
        <p>Capital: {engine.capital}</p>
        <p>Positions: {len(engine.positions)}</p>
        <p>Wins: {engine.stats.get("wins", 0)}</p>
        <p>Losses: {engine.stats.get("losses", 0)}</p>
        <p>Regime: {getattr(engine, "regime", "unknown")}</p>

        <hr/>

        <h3>Endpoints</h3>
        <ul>
            <li>/status</li>
            <li>/positions</li>
            <li>/trades</li>
            <li>/wallets</li>
            <li>/token-wallets</li>
            <li>/logs</li>
        </ul>
    </body>
    </html>
    """

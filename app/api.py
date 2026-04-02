from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import asyncio

from app.core.state import engine
from app.core.engine import main_loop
from app.alpha.wallet_alpha_v7 import wallet_trades, token_wallets

app = FastAPI()


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(main_loop())


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
def status():
    return {
        "capital": getattr(engine, "capital", 0),
        "positions": len(getattr(engine, "positions", [])),
        "wins": getattr(engine, "stats", {}).get("wins", 0),
        "losses": getattr(engine, "stats", {}).get("losses", 0),
        "running": getattr(engine, "running", False),
        "regime": getattr(engine, "regime", "unknown"),
    }


@app.get("/positions")
def positions():
    return getattr(engine, "positions", [])


@app.get("/trades")
def trades():
    return getattr(engine, "trade_history", [])[-50:]


@app.get("/wallets")
def wallets():
    return {
        "tracked_wallets": len(wallet_trades),
        "wallet_trades": dict(list(wallet_trades.items())[:20]),
    }


@app.get("/token-wallets")
def token_wallet_view():
    return dict(list(token_wallets.items())[:20])


@app.get("/logs")
def logs():
    return getattr(engine, "logs", [])[-100:]


@app.get("/", response_class=HTMLResponse)
def dashboard():
    capital = getattr(engine, "capital", 0)
    positions = len(getattr(engine, "positions", []))
    wins = getattr(engine, "stats", {}).get("wins", 0)
    losses = getattr(engine, "stats", {}).get("losses", 0)
    regime = getattr(engine, "regime", "unknown")

    return f"""
    <html>
    <body style="font-family: monospace; padding: 20px;">
        <h2>🔥 AI Trading System</h2>
        <p>Capital: {capital}</p>
        <p>Positions: {positions}</p>
        <p>Wins: {wins}</p>
        <p>Losses: {losses}</p>
        <p>Regime: {regime}</p>

        <hr/>

        <h3>Endpoints</h3>
        <ul>
            <li>/health</li>
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

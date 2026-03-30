from fastapi import FastAPI
import asyncio
from app.core.engine import main_loop
from app.core.state import engine

app = FastAPI(title="Integrated Trading Stack Merged")

@app.on_event("startup")
async def start():
    asyncio.create_task(main_loop())

@app.get("/")
def home():
    return {"status":"RUNNING","capital":engine.capital,"regime":engine.regime,"threshold":engine.threshold,"wallets":engine.wallets,"stats":engine.stats}

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/metrics")
def metrics():
    return {"capital":engine.capital,"regime":engine.regime,"threshold":engine.threshold,"trade_count":len(engine.trade_history),"recent_trades":engine.trade_history[-20:],"recent_logs":engine.logs[-50:],"wallets":engine.wallets,"stats":engine.stats}

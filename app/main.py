from fastapi import FastAPI
import asyncio

app = FastAPI()


@app.on_event("startup")
async def startup():
    print("🚀 SYSTEM START")
    try:
        from app.core.engine import main_loop
        asyncio.create_task(main_loop())
        print("✅ ENGINE STARTED")
    except Exception as e:
        print("❌ STARTUP IMPORT ERROR:", repr(e))


@app.get("/")
def root():
    return {"status": "RUNNING"}


@app.get("/debug")
def debug():
    from app.core.state import engine
    from app.core.risk_runtime import risk_engine

    return {
        "running": engine.running,
        "capital": engine.capital,
        "peak_capital": engine.peak_capital,
        "positions": engine.positions,
        "stats": engine.stats,
        "regime": engine.regime,
        "trade_history_count": len(engine.trade_history),
        "trade_history_tail": engine.trade_history[-20:],
        "risk": {
            "equity_peak": risk_engine.equity_peak,
            "drawdown": risk_engine.drawdown(engine.capital),
            "daily_realized_pnl": risk_engine.daily_realized_pnl,
            "daily_trades": risk_engine.daily_trades,
            "cooldown_until": risk_engine.cooldown_until,
            "manual_kill": risk_engine.manual_kill,
            "session_day": risk_engine.session_day,
        },
        "logs": engine.logs[-80:],
    }


@app.get("/health")
def health():
    from app.core.state import engine
    from app.core.risk_runtime import risk_engine

    return {
        "ok": engine.running,
        "capital": engine.capital,
        "positions": len(engine.positions),
        "errors": engine.stats.get("errors", 0),
        "drawdown": risk_engine.drawdown(engine.capital),
        "daily_realized_pnl": risk_engine.daily_realized_pnl,
        "daily_trades": risk_engine.daily_trades,
        "manual_kill": risk_engine.manual_kill,
    }


@app.post("/kill")
def kill():
    from app.core.state import engine
    from app.core.risk_runtime import risk_engine

    risk_engine.set_manual_kill(True)
    engine.log("🔴 MANUAL KILL")
    return {"ok": True, "manual_kill": True}


@app.post("/resume")
def resume():
    from app.core.state import engine
    from app.core.risk_runtime import risk_engine

    risk_engine.set_manual_kill(False)
    engine.log("🟢 MANUAL RESUME")
    return {"ok": True, "manual_kill": False}

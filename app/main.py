from fastapi import FastAPI
import asyncio
import time

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
    from app.core import engine as engine_module
    from app.portfolio.portfolio_manager import portfolio

    now = time.time()

    cooldown_view = {
        mint[:8]: round(max(0.0, ts - now + 30), 2)
        for mint, ts in list(engine_module.cooldown.items())[-20:]
    }

    candidates_view = {
        mint[:8]: {
            "age": round(now - meta.get("time", now), 2),
            "price": meta.get("price"),
            "score": meta.get("score"),
            "source": meta.get("source"),
            "breakout": meta.get("breakout"),
            "smart_money": meta.get("smart_money"),
            "liquidity": meta.get("liquidity"),
        }
        for mint, meta in list(engine_module.candidates.items())[-20:]
    }

    source_exposure = {}
    for src in ["breakout", "smart_money", "liquidity", "fusion", "unknown"]:
        ratio = portfolio.source_exposure_ratio(engine, src)
        if ratio > 0:
            source_exposure[src] = round(ratio, 4)

    positions_view = []
    for p in engine.positions:
        meta = p.get("meta", {})
        positions_view.append({
            "mint": p.get("mint"),
            "entry": p.get("entry"),
            "peak": p.get("peak"),
            "size": p.get("size"),
            "score": p.get("score"),
            "source": meta.get("source"),
            "breakout": meta.get("breakout"),
            "smart_money": meta.get("smart_money"),
            "liquidity": meta.get("liquidity"),
            "momentum": meta.get("momentum"),
            "held_sec": round(now - p.get("time", now), 2),
        })

    return {
        "running": engine.running,
        "capital": engine.capital,
        "peak_capital": engine.peak_capital,
        "regime": engine.regime,
        "positions": positions_view,
        "stats": engine.stats,
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
        "portfolio": {
            "total_exposure_ratio": round(portfolio.total_exposure_ratio(engine), 4),
            "can_add_more": portfolio.can_add_more(engine, max_exposure=0.75),
            "source_exposure_ratio": source_exposure,
        },
        "router_state": {
            "cooldown_count": len(engine_module.cooldown),
            "candidate_count": len(engine_module.candidates),
            "cooldown_tail": cooldown_view,
            "candidate_tail": candidates_view,
        },
        "logs": engine.logs[-120:],
    }


@app.get("/health")
def health():
    from app.core.state import engine
    from app.core.risk_runtime import risk_engine
    from app.portfolio.portfolio_manager import portfolio

    return {
        "ok": engine.running,
        "capital": engine.capital,
        "positions": len(engine.positions),
        "errors": engine.stats.get("errors", 0),
        "drawdown": risk_engine.drawdown(engine.capital),
        "daily_realized_pnl": risk_engine.daily_realized_pnl,
        "daily_trades": risk_engine.daily_trades,
        "manual_kill": risk_engine.manual_kill,
        "regime": engine.regime,
        "total_exposure_ratio": round(portfolio.total_exposure_ratio(engine), 4),
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

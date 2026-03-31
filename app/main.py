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
    return {
        "running": engine.running,
        "capital": engine.capital,
        "peak_capital": engine.peak_capital,
        "positions": engine.positions,
        "stats": engine.stats,
        "regime": engine.regime,
        "logs": engine.logs[-80:],
    }

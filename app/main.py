from fastapi import FastAPI
import asyncio

app = FastAPI()


@app.on_event("startup")
async def startup():
    print("🚀 SYSTEM START")
    try:
        from app.core.engine import main_loop
        asyncio.create_task(main_loop())
        print("✅ ENGINE TASK CREATED")
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
        "positions": engine.positions,
        "stats": engine.stats,
        "logs": engine.logs[-50:],
    }

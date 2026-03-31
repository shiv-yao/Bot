from fastapi import FastAPI
import asyncio

app = FastAPI()

@app.on_event("startup")
async def startup():
    print("🚀 SYSTEM START")

    from app.core.engine import main_loop
    asyncio.create_task(main_loop())

@app.get("/debug")
def debug():
    from app.core.state import engine
    return {
        "capital": engine.capital,
        "positions": engine.positions,
        "stats": engine.stats,
        "logs": engine.logs[-50:]
    }

from fastapi import FastAPI
import asyncio

from app.engine import main_loop
from app.state import engine
from app.metrics import compute_metrics

app = FastAPI()


@app.on_event("startup")
async def startup():
    asyncio.create_task(main_loop())


@app.get("/")
def root():
    return {
        "capital": engine.capital,
        "positions": len(engine.positions),
    }


@app.get("/metrics")
def metrics():
    return compute_metrics(engine)

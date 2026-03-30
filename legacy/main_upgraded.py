"""Optional upgraded entrypoint.

This file does not replace the current FastAPI app in main.py. It offers a
clean pipeline object that other modules can import incrementally.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from core.architecture import build_default_orchestrator
from state import engine

orchestrator = build_default_orchestrator()
app = FastAPI(title="Pump Trading Engine - Upgraded Architecture")


@app.get("/")
async def root():
    return {
        "ok": True,
        "message": "Pump Trading Engine Upgraded Architecture Ready",
        "mode": engine.mode,
    }


@app.get("/architecture")
async def architecture_status():
    return JSONResponse({
        "signal_bus_size": orchestrator.bus.size(),
        "positions": len(orchestrator.portfolio.positions),
        "candidate_count": engine.candidate_count,
        "engine_allocator": dict(engine.engine_allocator),
    })

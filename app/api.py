from fastapi import FastAPI
from fastapi.responses import JSONResponse
import asyncio
import traceback

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
        "capital": getattr(engine, "capital", 0.0),
        "positions": len(getattr(engine, "positions", []) or []),
        "running": bool(getattr(engine, "running", False)),
    }


@app.get("/metrics")
def metrics():
    try:
        return compute_metrics(engine)
    except Exception as e:
        tb = traceback.format_exc()
        engine.logs.append(f"METRICS_ERR {e}")
        engine.logs.append(tb)
        engine.logs[:] = engine.logs[-300:]
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(e),
                "where": "metrics",
            },
        )


@app.get("/logs")
def logs():
    return [str(x) for x in (getattr(engine, "logs", []) or [])[-100:]]

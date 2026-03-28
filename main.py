from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()

@app.get("/")
async def root():
    return {"ok": True, "status": "running"}

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/data")
async def data():
    return JSONResponse({"ok": True, "status": "running"})

@app.get("/metrics")
async def metrics():
    return {
        "positions": [],
        "signals": 0,
        "errors": 0
    }

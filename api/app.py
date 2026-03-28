from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()

@app.get("/")
async def root():
    return {"ok": True, "msg": "bot alive"}

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/data")
async def data():
    return JSONResponse({"ok": True, "status": "running"})

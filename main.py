from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()

@app.get("/")
async def root():
    return {"ok": True, "msg": "app alive"}

@app.get("/health")
async def health():
    return JSONResponse({"ok": True})

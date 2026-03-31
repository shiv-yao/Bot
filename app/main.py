from fastapi import FastAPI
import asyncio

app = FastAPI()

@app.on_event("startup")
async def startup():
    try:
        from app.core.engine import main_loop
        asyncio.create_task(main_loop())
        print("ENGINE STARTED")
    except Exception as e:
        print("ENGINE IMPORT ERROR:", repr(e))

@app.get("/")
def root():
    return {"status": "BOOT OK"}

@app.get("/debug")
def debug():
    return {"ok": True}

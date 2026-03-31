import asyncio
from fastapi import FastAPI
from app.core.engine import main_loop

app = FastAPI()


@app.on_event("startup")
async def startup():
    asyncio.create_task(main_loop())


@app.get("/")
def root():
    return {"status": "BOOT OK"}


@app.get("/debug")
def debug():
    return {"ok": True}

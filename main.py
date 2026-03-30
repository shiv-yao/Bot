from fastapi import FastAPI
from app.engine import engine_loop
import asyncio

app = FastAPI()

@app.on_event("startup")
async def start():
    asyncio.create_task(engine_loop())

@app.get("/")
def root():
    return {"status": "running"}

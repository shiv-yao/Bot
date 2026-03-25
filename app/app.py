from fastapi import FastAPI
import asyncio
from bot import bot_loop

app = FastAPI()

@app.on_event("startup")
async def startup():
    asyncio.create_task(bot_loop())

@app.get("/")
def root():
    return {"status": "ok"}

@app.get("/health")
def health():
    return {"healthy": True}

@app.get("/ping")
def ping():
    return {"msg": "pong"}

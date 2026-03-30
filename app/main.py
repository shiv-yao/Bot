
from fastapi import FastAPI
import asyncio
from app.engine import bot_loop

app = FastAPI()

@app.on_event("startup")
async def start():
    asyncio.create_task(bot_loop())

@app.get("/")
def home():
    return {"status":"running"}

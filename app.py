from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
import asyncio
from bot import bot_loop
from state import engine
from db import fetch_recent_trades

app = FastAPI(title="Multi-Position Institutional Dashboard")
templates = Jinja2Templates(directory="templates")

@app.on_event("startup")
async def startup():
    asyncio.create_task(bot_loop())

@app.get("/")
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/data")
def data():
    payload = dict(engine)
    payload["trade_history"] = fetch_recent_trades(50)
    return JSONResponse(payload)

@app.get("/health")
def health():
    return {"ok": True}

from fastapi.templating import Jinja2Templates
from fastapi import Request

templates = Jinja2Templates(directory="templates")

@app.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

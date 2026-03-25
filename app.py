
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
import asyncio
from bot import bot_loop
from state import engine

app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.on_event("startup")
async def startup():
    asyncio.create_task(bot_loop())

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/data")
def data():
    return engine

@app.get("/health")
def health():
    return {"ok": True}

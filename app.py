from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/data")
async def data():
    return JSONResponse({
        "status": "ok",
        "message": "dashboard backend alive"
    })

@app.get("/health")
async def health():
    return {"ok": True}

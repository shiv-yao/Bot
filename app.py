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
        "running": True,
        "mode": "PAPER",
        "sol_balance": 0.0,
        "capital": 0.0,
        "last_signal": "",
        "last_trade": "",
        "positions": [],
        "logs": [],
        "stats": {
            "signals": 0,
            "buys": 0,
            "sells": 0,
            "errors": 0
        },
        "trade_history": []
    })

@app.get("/health")
async def health():
    return {"ok": True}

if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

from fastapi import FastAPI
import asyncio

app = FastAPI()

@app.get("/")
def home():
    return {"status": "RUNNING"}

# 🔥 關鍵在這裡
@app.on_event("startup")
async def start():
    try:
        from app.core.engine import main_loop
        asyncio.create_task(main_loop())
        print("ENGINE STARTED")
    except Exception as e:
        print("ENGINE ERROR:", e)

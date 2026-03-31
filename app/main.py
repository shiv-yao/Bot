from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"status": "BOOT OK"}

# 🔥 延遲 import（關鍵）
@app.on_event("startup")
async def start():
    try:
        import asyncio
        from app.core.engine import main_loop
        asyncio.create_task(main_loop())
    except Exception as e:
        print("ENGINE FAIL:", e)

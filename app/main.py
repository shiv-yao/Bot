from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"status": "BOOT OK"}

# 🔥 延遲 import（關鍵）
@app.on_event("startup")
async def start():
    import asyncio

    async def test_loop():
        while True:
            print("engine alive")
            await asyncio.sleep(5)

    asyncio.create_task(test_loop())

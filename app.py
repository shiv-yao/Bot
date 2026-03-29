from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {"ok": True, "msg": "server running"}

@app.get("/health")
async def health():
    return {"ok": True}

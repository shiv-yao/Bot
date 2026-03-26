import os
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"status": "root ok"}

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/data")
def data():
    return {"data": "ok"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

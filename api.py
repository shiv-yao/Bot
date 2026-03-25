from fastapi import FastAPI
from .analysis import generate_report

app = FastAPI(title="Trading Insights API")

@app.get("/report")
def report():
    return generate_report().__dict__

@app.get("/health")
def health():
    return {"status": "ok"}

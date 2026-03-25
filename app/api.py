from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .config import DRY_RUN, MANUAL_CONFIRM, DEFAULT_INPUT_MINT
from .jupiter import search_tokens, get_price, get_order
from .risk import validate_order_ui_amount, validate_slippage_bps, ui_sol_to_lamports

app = FastAPI(title="Semi-live Jupiter API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TradeRequest(BaseModel):
    taker: str
    outputMint: str
    uiAmount: float
    slippageBps: int = 300
    confirm: bool = False

class SellRequest(BaseModel):
    taker: str
    inputMint: str
    uiAmount: float
    slippageBps: int = 300
    confirm: bool = False

@app.get("/health")
def health():
    return {"status": "ok", "dryRun": DRY_RUN, "manualConfirm": MANUAL_CONFIRM}

@app.get("/tokens")
def tokens(query: str = "SOL"):
    try:
        rows = search_tokens(query)
        ids = [r.get("id") for r in rows if r.get("id")]
        prices = get_price(ids)
        for r in rows:
            p = prices.get(r.get("id"), {}) if isinstance(prices, dict) else {}
            r["usdPrice"] = p.get("usdPrice")
            r["priceSource"] = p.get("priceSource")
        return {"count": len(rows), "items": rows}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.post("/trade/buy")
def trade_buy(req: TradeRequest):
    try:
        validate_order_ui_amount(req.uiAmount)
        validate_slippage_bps(req.slippageBps)
        if MANUAL_CONFIRM and not req.confirm:
            raise HTTPException(status_code=400, detail="Manual confirmation required")
        amount = ui_sol_to_lamports(req.uiAmount)
        if DRY_RUN:
            return {
                "mode": "dry_run",
                "action": "buy",
                "inputMint": DEFAULT_INPUT_MINT,
                "outputMint": req.outputMint,
                "amount": amount,
                "taker": req.taker,
                "slippageBps": req.slippageBps
            }
        order = get_order(DEFAULT_INPUT_MINT, req.outputMint, amount, req.taker, req.slippageBps)
        return {"mode": "live_order_created", "order": order}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/trade/sell")
def trade_sell(req: SellRequest):
    try:
        validate_order_ui_amount(req.uiAmount)
        validate_slippage_bps(req.slippageBps)
        if MANUAL_CONFIRM and not req.confirm:
            raise HTTPException(status_code=400, detail="Manual confirmation required")
        amount = str(int(req.uiAmount))
        if DRY_RUN:
            return {
                "mode": "dry_run",
                "action": "sell",
                "inputMint": req.inputMint,
                "outputMint": DEFAULT_INPUT_MINT,
                "amount": amount,
                "taker": req.taker,
                "slippageBps": req.slippageBps
            }
        order = get_order(req.inputMint, DEFAULT_INPUT_MINT, amount, req.taker, req.slippageBps)
        return {"mode": "live_order_created", "order": order}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

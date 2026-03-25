from typing import Any, Dict, List
import requests
from .config import JUP_API_KEY, JUP_BASE_URL

HEADERS = {}
if JUP_API_KEY:
    HEADERS["x-api-key"] = JUP_API_KEY

def _get(path: str, params: Dict[str, Any] | None = None) -> Any:
    r = requests.get(f"{JUP_BASE_URL}{path}", headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def _post(path: str, payload: Dict[str, Any]) -> Any:
    r = requests.post(f"{JUP_BASE_URL}{path}", headers={**HEADERS, "Content-Type":"application/json"}, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

def search_tokens(query: str) -> List[dict]:
    return _get("/tokens/v2/search", {"query": query})

def get_price(ids: List[str]) -> Dict[str, Any]:
    if not ids:
        return {}
    return _get("/price/v3", {"ids": ",".join(ids)})

def get_order(input_mint: str, output_mint: str, amount: str, taker: str, slippage_bps: int):
    return _get("/swap/v2/order", {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "taker": taker,
        "slippageBps": str(slippage_bps),
    })

def execute_order(request_id: str, signed_transaction: str):
    return _post("/swap/v2/execute", {
        "requestId": request_id,
        "signedTransaction": signed_transaction
    })

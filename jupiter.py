import base64
import os
from typing import Any, Dict, Optional

import httpx
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

JUP_BASE = "https://api.jup.ag/swap/v2"

def _headers() -> dict:
    api_key = os.getenv("JUP_API_KEY", "").strip()
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key
    return headers

async def get_order(
    input_mint: str,
    output_mint: str,
    amount_atomic: int,
    taker: str,
):
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://quote-api.jup.ag/v6/quote",   # 🔥 改這裡
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount_atomic),
                "slippageBps": 100,
            },
        )

        if resp.status_code != 200:
            return None

        data = resp.json()
        if "data" not in data or not data["data"]:
            return None

        return data["data"][0]   # 🔥 直接拿第一條 route
async def execute_order(route, keypair):
    async with httpx.AsyncClient(timeout=30) as client:
        swap = await client.post(
            "https://quote-api.jup.ag/v6/swap",
            json={
                "route": route,
                "userPublicKey": str(keypair.pubkey()),
                "wrapUnwrapSOL": True,
            },
        )

        if swap.status_code != 200:
            return None

        swap_data = swap.json()
        tx = swap_data.get("swapTransaction")

        if not tx:
            return None

        import base64
        from solders.transaction import VersionedTransaction

        raw_tx = VersionedTransaction.from_bytes(base64.b64decode(tx))
        signed = VersionedTransaction(raw_tx.message, [keypair])

        sig = await client.post(
            RPC,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    base64.b64encode(bytes(signed)).decode(),
                    {"skipPreflight": True},
                ],
            },
        )

        return sig.json()
async def execute_order(
    order: Dict[str, Any],
    keypair: Keypair,
) -> Optional[Dict[str, Any]]:
    tx_b64 = order.get("transaction")
    request_id = order.get("requestId")
    if not tx_b64 or not request_id:
        return None

    raw_tx = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
    signed_tx = VersionedTransaction(raw_tx.message, [keypair])
    signed_b64 = base64.b64encode(bytes(signed_tx)).decode()

    payload = {
        "signedTransaction": signed_b64,
        "requestId": request_id,
    }
    if order.get("lastValidBlockHeight") is not None:
        payload["lastValidBlockHeight"] = order["lastValidBlockHeight"]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{JUP_BASE}/execute",
            json=payload,
            headers={"Content-Type": "application/json", **_headers()},
        )
        if resp.status_code >= 400:
            return None
        return resp.json()

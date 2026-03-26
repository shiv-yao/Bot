import httpx
from smart_wallets import SMART_WALLETS

async def wallet_graph_signal(RPC):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for wallet in SMART_WALLETS:
                r = await client.post(
                    RPC,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [wallet, {"limit": 3}],
                    },
                )

                data = r.json()
                if "result" not in data:
                    continue

                txs = data["result"]

                for tx in txs:
                    sig = tx["signature"]

                    # 👉 模擬 decode（簡化版）
                    if "buy" in sig:  # placeholder
                        return "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

        return None

    except Exception:
        return None

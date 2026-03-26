import json
import asyncio
import httpx
import websockets

RPC_WS = "wss://api.mainnet-beta.solana.com"
RPC_HTTP = "https://api.mainnet-beta.solana.com"

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

IGNORE_MINTS = {
    SOL_MINT,
    USDC_MINT,
}


async def get_transaction(signature: str):
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                RPC_HTTP,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        signature,
                        {
                            "commitment": "confirmed",
                            "maxSupportedTransactionVersion": 0,
                            "encoding": "json",
                        },
                    ],
                },
            )

        if r.status_code != 200:
            return None

        data = r.json()
        return data.get("result")
    except Exception:
        return None


def extract_mints_from_balances(tx: dict):
    result = []

    meta = tx.get("meta") or {}
    for key in ["postTokenBalances", "preTokenBalances"]:
        for row in meta.get(key, []) or []:
            mint = row.get("mint")
            if not mint:
                continue
            if mint in IGNORE_MINTS:
                continue
            if len(mint) < 32 or len(mint) > 44:
                continue
            result.append(mint)

    # 去重
    seen = set()
    deduped = []
    for m in result:
        if m not in seen:
            seen.add(m)
            deduped.append(m)

    return deduped


async def decode_signature_to_mints(signature: str):
    tx = await get_transaction(signature)
    if not tx:
        return []

    return extract_mints_from_balances(tx)


async def mempool_stream(callback):
    async with websockets.connect(RPC_WS, ping_interval=20, ping_timeout=20) as ws:
        sub = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                "all",
                {"commitment": "processed"}
            ]
        }

        await ws.send(json.dumps(sub))

        while True:
            raw = await ws.recv()
            data = json.loads(raw)

            if "params" not in data:
                continue

            value = data["params"]["result"]["value"]
            signature = value.get("signature")
            err = value.get("err")
            logs = value.get("logs", [])

            if err is not None:
                continue

            # 先用 logs 粗篩，減少 getTransaction 次數
            joined = " ".join(logs).lower()
            if not any(x in joined for x in ["swap", "liquidity", "raydium", "jupiter"]):
                continue

            if not signature:
                continue

            # 給 RPC 一點時間，避免 processed 時 getTransaction 還抓不到
            await asyncio.sleep(0.4)

            mints = await decode_signature_to_mints(signature)
            for mint in mints:
                await callback({
                    "type": "swap",
                    "signature": signature,
                    "mint": mint,
                    "logs": logs,
                })

import httpx

SMART_WALLETS = [
    # 之後把真地址放這裡
]


async def get_wallet_tokens(rpc: str, wallet: str):
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccountsByOwner",
                    "params": [
                        wallet,
                        {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                        {"encoding": "jsonParsed"},
                    ],
                },
            )

        data = r.json()
        tokens = []

        for item in data.get("result", {}).get("value", []):
            info = item["account"]["data"]["parsed"]["info"]
            mint = info["mint"]
            amount = float(info["tokenAmount"].get("uiAmount") or 0)
            if amount > 0:
                tokens.append(mint)

        return tokens
    except Exception:
        return []


async def wallet_graph_signal(rpc: str):
    for wallet in SMART_WALLETS:
        tokens = await get_wallet_tokens(rpc, wallet)
        if tokens:
            return tokens[0]
    return None

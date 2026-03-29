import httpx

# 🔥 你可以自己加強這份清單（超關鍵）
SMART_WALLETS = [
    # 放你之後追蹤的大戶
]

RPC_DEFAULT = "https://api.mainnet-beta.solana.com"


async def get_recent_signatures(wallet: str, rpc: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [wallet, {"limit": 5}],
                },
            )

        return r.json()["result"]
    except:
        return []


async def get_tx(sig: str, rpc: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [sig, {"encoding": "jsonParsed"}],
                },
            )

        return r.json().get("result")
    except:
        return None


async def extract_mint(tx):
    try:
        instructions = tx["transaction"]["message"]["instructions"]

        for ix in instructions:
            if "parsed" in ix:
                info = ix["parsed"].get("info", {})
                mint = info.get("mint")
                if mint:
                    return mint

    except:
        pass

    return None


async def insider_signal(rpc: str):
    rpc = rpc or RPC_DEFAULT

    for wallet in SMART_WALLETS:
        sigs = await get_recent_signatures(wallet, rpc)

        for s in sigs:
            tx = await get_tx(s["signature"], rpc)
            if not tx:
                continue

            mint = await extract_mint(tx)

            if mint:
                return mint

    return None

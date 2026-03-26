import httpx
import asyncio

WALLET_CACHE = {}

async def get_signatures(RPC, address, limit=10):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(RPC, json={
                "jsonrpc":"2.0",
                "id":1,
                "method":"getSignaturesForAddress",
                "params":[address, {"limit": limit}]
            })
        return r.json()["result"]
    except:
        return []

async def get_tx(RPC, sig):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(RPC, json={
                "jsonrpc":"2.0",
                "id":1,
                "method":"getTransaction",
                "params":[sig, {"encoding":"jsonParsed"}]
            })
        return r.json()["result"]
    except:
        return None


async def extract_wallets_from_mints(RPC, mints):
    wallets = set()

    for mint in list(mints)[-20:]:
        sigs = await get_signatures(RPC, mint, 5)

        for s in sigs:
            sig = s.get("signature")
            tx = await get_tx(RPC, sig)
            if not tx:
                continue

            try:
                keys = tx["transaction"]["message"]["accountKeys"]
                for k in keys:
                    if isinstance(k, dict):
                        wallets.add(k["pubkey"])
                    else:
                        wallets.add(k)
            except:
                continue

    return list(wallets)


async def track_wallet_behavior(RPC, wallets):
    results = []

    for w in wallets[:20]:
        sigs = await get_signatures(RPC, w, 3)

        tokens = set()

        for s in sigs:
            tx = await get_tx(RPC, s.get("signature"))
            if not tx:
                continue

            try:
                instructions = tx["transaction"]["message"]["instructions"]
                for ins in instructions:
                    if isinstance(ins, dict):
                        program = ins.get("programId")
                        if program:
                            tokens.add(program)
            except:
                continue

        if tokens:
            results.append({
                "wallet": w,
                "tokens": list(tokens)
            })

    return results

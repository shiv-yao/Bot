import httpx
import random

SMART_WALLETS = [
    # 👉 你可以之後補真地址
]

async def get_wallet_tokens(rpc, wallet):
    async with httpx.AsyncClient() as client:
        r = await client.post(rpc, json={
            "jsonrpc":"2.0",
            "id":1,
            "method":"getTokenAccountsByOwner",
            "params":[
                wallet,
                {"programId":"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding":"jsonParsed"}
            ]
        })

    data = r.json()

    tokens = []
    for item in data.get("result", {}).get("value", []):
        info = item["account"]["data"]["parsed"]["info"]
        mint = info["mint"]
        amount = float(info["tokenAmount"]["uiAmount"] or 0)

        if amount > 0:
            tokens.append(mint)

    return tokens


async def wallet_graph_signal(rpc):
    for w in SMART_WALLETS:
        tokens = await get_wallet_tokens(rpc, w)
        if tokens:
            return random.choice(tokens)

    return None

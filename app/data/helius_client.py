import os
import httpx

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "")

async def fetch_wallets(mint: str):
    if not HELIUS_KEY:
        return []

    url = f"https://api.helius.xyz/v0/addresses/{mint}/transactions?api-key={HELIUS_KEY}"

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url)
            data = r.json()
    except:
        return []

    wallets = set()

    for tx in data[:25]:
        for t in tx.get("tokenTransfers", []):
            if t.get("mint") == mint:
                if t.get("toUserAccount"):
                    wallets.add(t["toUserAccount"])

    return list(wallets)[:15]

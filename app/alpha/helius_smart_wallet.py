import os
import httpx

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "")


async def fetch_smart_wallets(mint: str):

    if not HELIUS_KEY:
        return []

    url = f"https://api.helius.xyz/v0/token-transfers?api-key={HELIUS_KEY}"

    payload = {
        "mint": mint,
        "limit": 50,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=payload)

        if r.status_code != 200:
            return []

        data = r.json()
        if not isinstance(data, list):
            return []

    except Exception:
        return []

    wallets = set()

    for tx in data:
        try:
            w1 = tx.get("fromUserAccount")
            w2 = tx.get("toUserAccount")

            if w1:
                wallets.add(w1)
            if w2:
                wallets.add(w2)

        except:
            continue

    return list(wallets)

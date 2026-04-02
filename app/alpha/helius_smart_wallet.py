import os
import httpx

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "")

async def fetch_smart_wallets(mint: str):
    if not HELIUS_KEY or not mint:
        return []

    url = f"https://api.helius.xyz/v0/token-transfers?api-key={HELIUS_KEY}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={
                "mint": mint,
                "limit": 50
            })

            if r.status_code != 200:
                return []

            data = r.json()
    except Exception:
        return []

    wallets = []

    for tx in data:
        w = tx.get("toUserAccount")
        if w and isinstance(w, str):
            wallets.append(w)

    # 🔥 去重 + early wallets
    return list(dict.fromkeys(wallets))[:20]

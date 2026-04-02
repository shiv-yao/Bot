import os
import httpx

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "").strip()


async def fetch_smart_wallets(mint: str) -> list[str]:
    if not HELIUS_KEY or not mint:
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

            if w1 and isinstance(w1, str):
                wallets.add(w1)
            if w2 and isinstance(w2, str):
                wallets.add(w2)
        except Exception:
            continue

    return list(wallets)[:20]

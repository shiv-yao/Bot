import os
import httpx

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "")

async def fetch_smart_wallets(mint: str):
    if not HELIUS_KEY or not mint:
        return []

    url = f"https://api.helius.xyz/v0/addresses/{mint}/transactions?api-key={HELIUS_KEY}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)

            if r.status_code != 200:
                return []

            data = r.json()
    except Exception:
        return []

    wallets = []

    for tx in data[:30]:
        try:
            for acc in tx.get("accountData", []):
                addr = acc.get("account")
                if addr:
                    wallets.append(addr)
        except:
            continue

    return list(dict.fromkeys(wallets))[:20]

import os
import httpx

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "")


async def fetch_smart_wallets(mint: str):

    if not HELIUS_KEY:
        return []

    url = f"https://api.helius.xyz/v0/addresses/{mint}/transactions?api-key={HELIUS_KEY}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)

        if r.status_code != 200:
            return []

        data = r.json()
        if not isinstance(data, list):
            return []

    except Exception:
        return []

    wallets = set()

    for tx in data[:25]:
        try:
            accounts = tx.get("accountData", [])
            for acc in accounts:
                addr = acc.get("account")
                if addr:
                    wallets.add(addr)
        except:
            continue

    return list(wallets)

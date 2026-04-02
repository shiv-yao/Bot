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
            if not isinstance(data, list):
                return []
    except Exception:
        return []

    wallets = set()

    for tx in data[:30]:
        try:
            for t in tx.get("tokenTransfers", []) or []:
                if t.get("mint") == mint:
                    to_wallet = t.get("toUserAccount")
                    from_wallet = t.get("fromUserAccount")

                    if to_wallet:
                        wallets.add(to_wallet)
                    if from_wallet:
                        wallets.add(from_wallet)

            for n in tx.get("nativeTransfers", []) or []:
                to_wallet = n.get("toUserAccount")
                from_wallet = n.get("fromUserAccount")

                if to_wallet:
                    wallets.add(to_wallet)
                if from_wallet:
                    wallets.add(from_wallet)
        except Exception:
            continue

    return list(wallets)[:20]

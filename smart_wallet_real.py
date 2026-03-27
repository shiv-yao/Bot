print("SMART_WALLET_REAL_LOADED")

import os
import httpx

HELIUS = "https://api.helius.xyz/v0"
API_KEY = os.getenv("HELIUS_API_KEY", "").strip()


async def get_signatures(mint: str):
    if not mint:
        return []

    if not API_KEY:
        return []

    try:
        url = f"{HELIUS}/addresses/{mint}/transactions?api-key={API_KEY}"

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)

        if r.status_code != 200:
            return []

        data = r.json()
        if not isinstance(data, list):
            return []

        return data[:20]
    except Exception:
        return []


async def extract_wallets_from_tx(tx_list):
    wallets = set()

    for tx in tx_list:
        try:
            accounts = tx.get("accounts", [])
            if not isinstance(accounts, list):
                continue

            for acc in accounts:
                if isinstance(acc, str) and len(acc) > 30:
                    wallets.add(acc)
        except Exception:
            continue

    return list(wallets)


async def real_smart_wallets(RPC, candidates):
    wallets = set()

    if not candidates:
        return []

    for mint in list(candidates)[:10]:
        txs = await get_signatures(mint)
        ws = await extract_wallets_from_tx(txs)

        for w in ws:
            wallets.add(w)

    return list(wallets)[:20]


async def real_smart_signal(RPC, wallets, candidates):
    if not wallets:
        return None

    if not candidates:
        return None

    for mint in candidates:
        return mint

    return None

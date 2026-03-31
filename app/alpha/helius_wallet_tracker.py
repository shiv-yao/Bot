import os
import httpx
from collections import defaultdict

from app.alpha.insider_engine import record_early_wallets

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "").strip()

token_wallets = defaultdict(set)


async def fetch_token_trades(mint: str):
    if not HELIUS_KEY:
        return []

    url = f"https://api.helius.xyz/v0/token-transfers?api-key={HELIUS_KEY}"

    payload = {
        "mint": mint,
        "limit": 20,
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

    wallets = []

    for tx in data:
        try:
            w = tx.get("toUserAccount")
            if w:
                wallets.append(w)
        except:
            continue

    return list(set(wallets))


async def update_token_wallets(mint: str):
    wallets = await fetch_token_trades(mint)

    if not wallets:
        return

    for w in wallets:
        token_wallets[mint].add(w)

    # 🔥 early insider
    record_early_wallets(mint, wallets)

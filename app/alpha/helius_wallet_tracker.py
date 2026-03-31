import os
import httpx
from collections import defaultdict
from app.alpha.wallet_graph import update_graph
from app.alpha.insider_engine import record_early_wallets

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "").strip()

# token -> wallets
token_wallets = defaultdict(set)


async def fetch_token_trades(mint: str) -> list[str]:
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

    wallets = []
    for tx in data:
        try:
            to_wallet = tx.get("toUserAccount")
            if to_wallet and isinstance(to_wallet, str):
                wallets.append(to_wallet)
        except Exception:
            continue

    seen = set()
    unique_wallets = []
    for w in wallets:
        if w not in seen:
            seen.add(w)
            unique_wallets.append(w)

    return unique_wallets


async def update_token_wallets(mint: str):
    wallets = await fetch_token_trades(mint)
    if not wallets:
        return

    for w in wallets:
        token_wallets[mint].add(w)

    update_graph(mint, wallets)
    record_early_wallets(mint, wallets)


def get_wallets_for_token(mint: str) -> list[str]:
    return list(token_wallets.get(mint, set()))

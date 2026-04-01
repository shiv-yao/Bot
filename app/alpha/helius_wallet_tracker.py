import os
import httpx
from collections import defaultdict

from app.alpha.insider_engine import record_early_wallets
from app.alpha.wallet_alpha import record_token_wallets

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "").strip()

token_wallets = defaultdict(set)


async def fetch_token_trades_v1(mint: str) -> list[str]:
    if not HELIUS_KEY or not mint:
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
            if w and isinstance(w, str):
                wallets.append(w)
        except Exception:
            continue

    return list(dict.fromkeys(wallets))


async def fetch_token_trades_v2(mint: str) -> list[str]:
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

    wallets = []

    for tx in data[:20]:
        try:
            for acc in tx.get("accountData", []) or []:
                addr = acc.get("account")
                if addr and isinstance(addr, str):
                    wallets.append(addr)
        except Exception:
            continue

    return list(dict.fromkeys(wallets))


async def fetch_wallets(mint: str) -> list[str]:
    w1 = await fetch_token_trades_v1(mint)
    if w1:
        return w1

    w2 = await fetch_token_trades_v2(mint)
    if w2:
        return w2

    return []


async def update_token_wallets(mint: str) -> list[str]:
    wallets = await fetch_wallets(mint)

    if not wallets:
        return []

    for w in wallets:
        token_wallets[mint].add(w)

    record_early_wallets(mint, wallets)
    record_token_wallets(mint, wallets)

    return wallets


def get_wallets_for_token(mint: str) -> list[str]:
    return list(token_wallets.get(mint, set()))

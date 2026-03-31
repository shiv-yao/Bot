import httpx
import asyncio
import os
from collections import defaultdict

HELIUS_KEY = os.getenv("HELIUS_API_KEY")

token_wallets = defaultdict(set)


async def fetch_token_trades(mint: str):
    url = f"https://api.helius.xyz/v0/token-transfers?api-key={HELIUS_KEY}"

    payload = {
        "mint": mint,
        "limit": 50,
    }

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(url, json=payload)
            data = r.json()
        except Exception:
            return []

    wallets = []

    for tx in data:
        try:
            if tx.get("toUserAccount"):
                wallets.append(tx["toUserAccount"])
        except Exception:
            continue

    return wallets


async def update_token_wallets(mint: str):
    wallets = await fetch_token_trades(mint)

    for w in wallets:
        token_wallets[mint].add(w)


def get_wallets_for_token(mint: str):
    return list(token_wallets.get(mint, []))

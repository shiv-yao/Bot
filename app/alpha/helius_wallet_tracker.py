import os
import httpx
from collections import defaultdict

from app.alpha.wallet_alpha import record_token_wallets

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "")
BASE = "https://api.helius.xyz/v0"

token_wallets = defaultdict(set)


def url(path):
    return f"{BASE}{path}?api-key={HELIUS_KEY}"


async def fetch_tx(mint):
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url(f"/addresses/{mint}/transactions"))

        if r.status_code != 200:
            return []

        data = r.json()
        return data[:20] if isinstance(data, list) else []
    except:
        return []


def extract_wallets(tx_list, mint):
    wallets = []

    for tx in tx_list:
        for t in tx.get("tokenTransfers", []) or []:
            if t.get("mint") != mint:
                continue

            w = t.get("toUserAccount")
            if w:
                wallets.append(w)

    return list(set(wallets))


async def update_token_wallets(mint):
    txs = await fetch_tx(mint)
    wallets = extract_wallets(txs, mint)

    if not wallets:
        wallets = [f"mint_{mint[:6]}"]

    for w in wallets:
        token_wallets[mint].add(w)

    record_token_wallets(mint, wallets)

    return wallets

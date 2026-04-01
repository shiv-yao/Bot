import os
import httpx
from collections import defaultdict

from app.alpha.insider_engine import record_early_wallets
from app.alpha.wallet_alpha import record_token_wallets

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "").strip()

token_wallets = defaultdict(set)


async def fetch_enhanced_transactions(mint: str):
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

            return data[:20]
    except Exception:
        return []


def extract_buyers_from_tx(tx_list, mint: str):
    buyers = []

    for tx in tx_list:
        try:
            token_transfers = tx.get("tokenTransfers", []) or []

            for t in token_transfers:
                if t.get("mint") != mint:
                    continue

                to_wallet = t.get("toUserAccount")
                if to_wallet and isinstance(to_wallet, str):
                    buyers.append(to_wallet)
        except Exception:
            continue

    return list(dict.fromkeys(buyers))


def fallback_wallet(mint: str):
    return f"mint_{mint[:6]}"


async def update_token_wallets(mint: str) -> list[str]:
    txs = await fetch_enhanced_transactions(mint)
    wallets = extract_buyers_from_tx(txs, mint)

    if not wallets:
        wallets = [fallback_wallet(mint)]

    for w in wallets:
        token_wallets[mint].add(w)

    record_early_wallets(mint, wallets)
    record_token_wallets(mint, wallets)

    return wallets


def get_wallets_for_token(mint: str) -> list[str]:
    return list(token_wallets.get(mint, set()))

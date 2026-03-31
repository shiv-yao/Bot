import os
import httpx
from collections import defaultdict

from app.alpha.wallet_graph import update_graph
from app.alpha.insider_engine import record_early_wallets

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "").strip()
BASE_URL = "https://api.helius.xyz"

# token -> wallets
token_wallets = defaultdict(set)

# 先放測試用追蹤地址
# 之後你可以換成真正的 smart wallets
TRACKED_ADDRESSES = [
    "So11111111111111111111111111111111111111112",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
]


async def fetch_address_transactions(address: str) -> list[dict]:
    """
    查某個地址最近的 enhanced transactions
    """
    if not HELIUS_KEY or not address:
        return []

    url = f"{BASE_URL}/v0/addresses/{address}/transactions?api-key={HELIUS_KEY}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []

            data = r.json()
            if not isinstance(data, list):
                return []

            return data
    except Exception:
        return []


def extract_wallets_for_mint_from_txs(mint: str, txs: list[dict]) -> list[str]:
    """
    從 enhanced transactions 裡找出和某個 mint 有關的 wallet
    """
    wallets = []

    for tx in txs:
        try:
            # 1. tokenTransfers
            for tt in tx.get("tokenTransfers", []) or []:
                if tt.get("mint") == mint:
                    to_wallet = tt.get("toUserAccount")
                    from_wallet = tt.get("fromUserAccount")

                    if to_wallet and isinstance(to_wallet, str):
                        wallets.append(to_wallet)

                    if from_wallet and isinstance(from_wallet, str):
                        wallets.append(from_wallet)

            # 2. accountData.tokenBalanceChanges
            for acc in tx.get("accountData", []) or []:
                for tbc in acc.get("tokenBalanceChanges", []) or []:
                    if tbc.get("mint") == mint:
                        user_wallet = tbc.get("userAccount")
                        if user_wallet and isinstance(user_wallet, str):
                            wallets.append(user_wallet)

        except Exception:
            continue

    # 去重保序
    seen = set()
    unique_wallets = []
    for w in wallets:
        if w not in seen:
            seen.add(w)
            unique_wallets.append(w)

    return unique_wallets


async def update_token_wallets(mint: str):
    """
    從追蹤地址反推某個 mint 的相關 wallet
    """
    if not mint or not TRACKED_ADDRESSES:
        return

    all_wallets = []

    for address in TRACKED_ADDRESSES:
        txs = await fetch_address_transactions(address)
        wallets = extract_wallets_for_mint_from_txs(mint, txs)
        all_wallets.extend(wallets)

    if not all_wallets:
        return

    # 去重保序
    seen = set()
    unique_wallets = []
    for w in all_wallets:
        if w not in seen:
            seen.add(w)
            unique_wallets.append(w)

    for w in unique_wallets:
        token_wallets[mint].add(w)

    # 更新 wallet graph
    update_graph(mint, unique_wallets)

    # 更新 insider early wallets
    record_early_wallets(mint, unique_wallets)


def get_wallets_for_token(mint: str) -> list[str]:
    return list(token_wallets.get(mint, set()))

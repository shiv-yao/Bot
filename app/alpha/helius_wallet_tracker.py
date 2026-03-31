import os
import httpx
from collections import defaultdict
from app.alpha.wallet_graph import update_graph
from app.alpha.insider_engine import record_early_wallets

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "").strip()
BASE_URL = "https://api-mainnet.helius-rpc.com"

# token -> wallets
token_wallets = defaultdict(set)

# 先放一批你要追蹤的地址；之後可換成你的 smart wallet 清單
TRACKED_ADDRESSES = [
    "J6TDXvarvpBdPXTaTU8eJbtso1PUCYKGkVtMKUUY8iEa",
    "87rRdssFiTJKY4MGARa4G5vQ31hmR7MxSmhzeaJ5AAxJ",
]


async def fetch_address_transactions(address: str) -> list[dict]:
    if not HELIUS_KEY or not address:
        return []

    url = f"{BASE_URL}/v0/addresses/{address}/transactions?api-key={HELIUS_KEY}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
            data = r.json()
            return data if isinstance(data, list) else []
    except Exception:
        return []


def extract_wallets_for_mint_from_txs(mint: str, txs: list[dict]) -> list[str]:
    wallets: list[str] = []

    for tx in txs:
        try:
            # 先看 tokenTransfers
            for tt in tx.get("tokenTransfers", []) or []:
                if tt.get("mint") == mint:
                    to_wallet = tt.get("toUserAccount")
                    from_wallet = tt.get("fromUserAccount")
                    if isinstance(to_wallet, str) and to_wallet:
                        wallets.append(to_wallet)
                    if isinstance(from_wallet, str) and from_wallet:
                        wallets.append(from_wallet)

            # 再看 accountData.tokenBalanceChanges
            for acc in tx.get("accountData", []) or []:
                for tbc in acc.get("tokenBalanceChanges", []) or []:
                    if tbc.get("mint") == mint:
                        user_wallet = tbc.get("userAccount")
                        if isinstance(user_wallet, str) and user_wallet:
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
    if not mint or not TRACKED_ADDRESSES:
        return

    all_wallets: list[str] = []

    for address in TRACKED_ADDRESSES:
        txs = await fetch_address_transactions(address)
        wallets = extract_wallets_for_mint_from_txs(mint, txs)
        all_wallets.extend(wallets)

    if not all_wallets:
        return

    seen = set()
    unique_wallets = []
    for w in all_wallets:
        if w not in seen:
            seen.add(w)
            unique_wallets.append(w)

    for w in unique_wallets:
        token_wallets[mint].add(w)

    update_graph(mint, unique_wallets)
    record_early_wallets(mint, unique_wallets)


def get_wallets_for_token(mint: str) -> list[str]:
    return list(token_wallets.get(mint, set()))

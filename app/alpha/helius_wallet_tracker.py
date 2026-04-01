import os
import httpx
from collections import defaultdict

from app.alpha.insider_engine import record_early_wallets
from app.alpha.wallet_alpha import record_token_wallets

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "").strip()
HELIUS_BASE = "https://api.helius.xyz/v0"
SOL_MINT = "So11111111111111111111111111111111111111112"

# mint -> wallets
token_wallets = defaultdict(set)


def helius_url(path: str) -> str:
    if HELIUS_KEY:
        return f"{HELIUS_BASE}{path}?api-key={HELIUS_KEY}"
    return f"{HELIUS_BASE}{path}"


async def get_address_transactions(address: str, limit: int = 20) -> list[dict]:
    """
    來自 smart_wallet_real.py 的核心思路：
    用 Helius Enhanced Transactions 抓某地址近期交易。
    這裡 address 可以是 wallet，也可以先拿 mint 試抓。
    """
    if not address:
        return []

    try:
        url = helius_url(f"/addresses/{address}/transactions")

        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(url)

        if r.status_code != 200:
            return []

        data = r.json()
        if not isinstance(data, list):
            return []

        return data[:limit]
    except Exception:
        return []


def extract_wallets_from_tx(tx: dict) -> list[str]:
    """
    zip 裡原本是抓 accounts。
    這裡升級成：
    1. 優先抓 tokenTransfers 的 toUserAccount / fromUserAccount
    2. 再 fallback 抓 accounts
    """
    wallets = []

    try:
        token_transfers = tx.get("tokenTransfers", []) or []
        for row in token_transfers:
            to_wallet = row.get("toUserAccount")
            from_wallet = row.get("fromUserAccount")

            if isinstance(to_wallet, str) and 32 <= len(to_wallet) <= 44:
                wallets.append(to_wallet)

            if isinstance(from_wallet, str) and 32 <= len(from_wallet) <= 44:
                wallets.append(from_wallet)
    except Exception:
        pass

    if not wallets:
        try:
            accounts = tx.get("accounts", []) or []
            for acc in accounts:
                if not isinstance(acc, str):
                    continue
                if len(acc) < 32 or len(acc) > 44:
                    continue
                wallets.append(acc)
        except Exception:
            pass

    dedup = []
    seen = set()
    for w in wallets:
        if w not in seen:
            seen.add(w)
            dedup.append(w)

    return dedup


def extract_mints_from_tx(tx: dict) -> list[str]:
    """
    直接沿用 zip 裡 smart_wallet_real.py 的核心邏輯。
    """
    mints = []

    try:
        token_transfers = tx.get("tokenTransfers", []) or []
        for row in token_transfers:
            mint = row.get("mint")
            if not mint:
                continue
            if mint == SOL_MINT:
                continue
            if len(mint) < 32 or len(mint) > 44:
                continue
            mints.append(mint)
    except Exception:
        pass

    dedup = []
    seen = set()
    for m in mints:
        if m not in seen:
            seen.add(m)
            dedup.append(m)

    return dedup


def extract_buyers_from_tx(tx_list: list[dict], mint: str) -> list[str]:
    """
    真 Helius 解析版核心：
    優先抓 tokenTransfers 裡收到該 mint 的 wallet。
    """
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

    dedup = []
    seen = set()
    for w in buyers:
        if w not in seen:
            seen.add(w)
            dedup.append(w)

    return dedup


def fallback_wallet(mint: str) -> str:
    return f"mint_{mint[:6]}"


async def fetch_token_wallets_from_mint_transactions(mint: str) -> list[str]:
    """
    直接抓 mint address 的 enhanced tx，再解析 tokenTransfers 買家。
    """
    txs = await get_address_transactions(mint, limit=20)
    if not txs:
        return []

    buyers = extract_buyers_from_tx(txs, mint)
    if buyers:
        return buyers

    # 如果沒抓到真 buyer，退而求其次抓交易中常見 wallet
    wallets = []
    for tx in txs:
        wallets.extend(extract_wallets_from_tx(tx))

    dedup = []
    seen = set()
    for w in wallets:
        if w not in seen:
            seen.add(w)
            dedup.append(w)

    return dedup[:20]


async def fetch_wallets(mint: str) -> list[str]:
    """
    對外主入口。
    """
    if not HELIUS_KEY or not mint:
        return []

    wallets = await fetch_token_wallets_from_mint_transactions(mint)
    return wallets


async def update_token_wallets(mint: str) -> list[str]:
    """
    寫回你現在整套系統會用到的 token_wallets / insider / wallet_alpha。
    """
    wallets = await fetch_wallets(mint)

    # fallback：至少讓 wallet alpha 能開始學
    if not wallets:
        wallets = [fallback_wallet(mint)]

    for w in wallets:
        token_wallets[mint].add(w)

    record_early_wallets(mint, wallets)
    record_token_wallets(mint, wallets)

    return wallets


def get_wallets_for_token(mint: str) -> list[str]:
    return list(token_wallets.get(mint, set()))

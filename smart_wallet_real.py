import httpx

HELIUS_BASE = "https://api.helius.xyz/v0"
HELIUS_API_KEY = ""  # 有 key 就填，沒有先留空

SOL_MINT = "So11111111111111111111111111111111111111112"


def helius_url(path: str) -> str:
    if HELIUS_API_KEY:
        return f"{HELIUS_BASE}{path}?api-key={HELIUS_API_KEY}"
    return f"{HELIUS_BASE}{path}"


async def get_address_transactions(address: str, limit: int = 20):
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


def extract_wallets_from_tx(tx: dict):
    wallets = []

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


def extract_mints_from_tx(tx: dict):
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


async def real_smart_wallets(RPC: str, candidates: set):
    """
    從候選 mint 的近期交易中，抽出常出現的 wallet。
    這是較偏 recall 的版本：寧可多抓一點，不要太嚴。
    """
    wallet_counts = {}

    sample_mints = list(candidates)[-12:]

    for mint in sample_mints:
        txs = await get_address_transactions(mint, limit=15)

        for tx in txs:
            wallets = extract_wallets_from_tx(tx)
            for w in wallets:
                wallet_counts[w] = wallet_counts.get(w, 0) + 1

    ranked = sorted(wallet_counts.items(), key=lambda x: x[1], reverse=True)
    return [w for w, _ in ranked[:20]]


async def real_smart_signal(RPC: str, wallets: list, candidates: set):
    """
    從 smart wallets 近期碰過的 mint 中，找也在 candidates 裡的標的。
    """
    if not wallets:
        return None

    if not candidates:
        return None

    for wallet in wallets[:10]:
        txs = await get_address_transactions(wallet, limit=10)

        for tx in txs:
            mints = extract_mints_from_tx(tx)
            for mint in mints:
                if mint in candidates and mint != SOL_MINT:
                    return mint

    return None

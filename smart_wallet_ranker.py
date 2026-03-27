print("SMART_WALLET_RANKER_LOADED")

import os
import httpx

CACHE = {}

HELIUS = "https://api.helius.xyz/v0"
API_KEY = os.getenv("HELIUS_API_KEY", "").strip()


async def get_wallet_txs(wallet: str):
    if not wallet:
        return []

    if not API_KEY:
        return []

    try:
        url = f"{HELIUS}/addresses/{wallet}/transactions?api-key={API_KEY}"

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)

        if r.status_code != 200:
            return []

        data = r.json()
        if not isinstance(data, list):
            return []

        return data[:50]
    except Exception:
        return []


def calc_wallet_score(txs):
    pnl = 0.0
    wins = 0
    trades = 0

    for tx in txs:
        try:
            val = tx.get("nativeTransfers", [])
            if not val:
                continue

            trades += 1

            amount = sum(float(v.get("amount", 0) or 0) for v in val)

            if amount > 0:
                pnl += amount
                wins += 1
            else:
                pnl += amount

        except Exception:
            continue

    if trades == 0:
        return 0.0

    winrate = wins / trades
    score = pnl * 0.7 + winrate * 100.0
    return score


async def rank_wallets(wallets):
    scored = []

    if not wallets:
        return []

    for w in wallets:
        if not w:
            continue

        if w in CACHE:
            scored.append((w, CACHE[w]))
            continue

        txs = await get_wallet_txs(w)
        score = calc_wallet_score(txs)

        CACHE[w] = score
        scored.append((w, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [w for w, s in scored if s > 0][:10]

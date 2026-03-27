import httpx

CACHE = {}


async def get_wallet_txs(wallet: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.helius.xyz/v0/addresses/{wallet}/transactions"
            )

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
            native_transfers = tx.get("nativeTransfers", [])
            if not native_transfers:
                continue

            amount = sum(float(v.get("amount", 0) or 0) for v in native_transfers)

            trades += 1

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

    # 簡化版分數
    score = (pnl * 0.7) + (winrate * 100.0)
    return score


async def rank_wallets(wallets):
    scored = []

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

    # 只保留正分 wallet，最多 10 個
    return [w for w, s in scored if s > 0][:10]

import httpx

CACHE = {}

async def get_wallet_txs(wallet: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://api.helius.xyz/v0/addresses/{wallet}/transactions")
        if r.status_code != 200:
            return []
        return r.json()[:50]
    except:
        return []


def calc_wallet_score(txs):
    pnl = 0
    wins = 0
    trades = 0

    for tx in txs:
        try:
            val = tx.get("nativeTransfers", [])
            if not val:
                continue

            trades += 1

            amount = sum(v.get("amount", 0) for v in val)

            if amount > 0:
                pnl += amount
                wins += 1
            else:
                pnl += amount

        except:
            continue

    if trades == 0:
        return 0

    winrate = wins / trades
    score = pnl * 0.7 + winrate * 100

    return score


async def rank_wallets(wallets):
    scored = []

    for w in wallets:
        if w in CACHE:
            scored.append((w, CACHE[w]))
            continue

        txs = await get_wallet_txs(w)
        score = calc_wallet_score(txs)

        CACHE[w] = score
        scored.append((w, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [w for w, s in scored if s > 0][:10]

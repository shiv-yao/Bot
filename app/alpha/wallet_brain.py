from collections import defaultdict

wallet_db = defaultdict(lambda: {
    "trades": 0,
    "wins": 0,
    "pnl": 0.0
})

def update_wallet(wallet, pnl):
    if not wallet:
        return

    w = wallet_db[wallet]
    w["trades"] += 1
    w["pnl"] += pnl

    if pnl > 0:
        w["wins"] += 1

def wallet_rank(wallet):
    w = wallet_db.get(wallet)

    if not w or w["trades"] < 3:
        return 0.05

    winrate = w["wins"] / w["trades"]
    avg = w["pnl"] / w["trades"]

    return min(winrate * 0.6 + max(avg, 0) * 4, 1.0)

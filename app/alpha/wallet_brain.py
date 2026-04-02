import json
from collections import defaultdict

DB_FILE = "wallet_db.json"

wallet_db = defaultdict(lambda: {
    "trades": 0,
    "wins": 0,
    "pnl": 0.0
})


# ===== LOAD =====
def load_wallet_db():
    global wallet_db
    try:
        with open(DB_FILE, "r") as f:
            data = json.load(f)
            wallet_db.update(data)
    except:
        pass


# ===== SAVE =====
def save_wallet_db():
    try:
        with open(DB_FILE, "w") as f:
            json.dump(wallet_db, f)
    except:
        pass


# ===== UPDATE =====
def update_wallet(wallet: str, pnl: float):
    if not wallet:
        return

    w = wallet_db[wallet]

    w["trades"] += 1
    w["pnl"] += pnl

    if pnl > 0:
        w["wins"] += 1


# ===== SCORE =====
def wallet_rank(wallet: str) -> float:
    w = wallet_db.get(wallet)

    if not w or w["trades"] < 3:
        return 0.05

    winrate = w["wins"] / w["trades"]
    avg = w["pnl"] / w["trades"]

    score = winrate * 0.6 + max(avg, 0) * 4
    return min(score, 1.0)


# ===== LEADERBOARD =====
def top_wallets(n=10):
    ranked = sorted(wallet_db.items(), key=lambda x: wallet_rank(x[0]), reverse=True)
    return ranked[:n]

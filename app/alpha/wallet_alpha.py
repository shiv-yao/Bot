from collections import defaultdict
import math

# =============================
# WALLET STORAGE
# =============================
wallet_stats = defaultdict(lambda: {
    "wins": 0,
    "losses": 0,
    "pnl": 0.0,
    "trades": 0,
})

token_wallet_map = defaultdict(set)

# =============================
# RECORD
# =============================
def record_wallet_result(wallet: str, pnl: float):
    if not wallet:
        return

    row = wallet_stats[wallet]
    row["trades"] += 1
    row["pnl"] += pnl

    if pnl >= 0:
        row["wins"] += 1
    else:
        row["losses"] += 1


def track_token_wallets(mint: str, wallets: list[str]):
    if not wallets:
        return

    for w in wallets[:20]:
        token_wallet_map[mint].add(w)


# =============================
# SCORE
# =============================
def wallet_score(wallet: str) -> float:
    row = wallet_stats.get(wallet)
    if not row or row["trades"] < 3:
        return 0.0

    win_rate = row["wins"] / max(row["trades"], 1)
    avg_pnl = row["pnl"] / max(row["trades"], 1)

    score = (win_rate * 0.6) + (avg_pnl * 2.0)

    return max(min(score, 1.0), 0.0)


def cluster_score(wallets: list[str]) -> float:
    if not wallets:
        return 0.0

    scores = [wallet_score(w) for w in wallets]
    if not scores:
        return 0.0

    top = sorted(scores, reverse=True)[:5]
    return sum(top) / len(top)


def get_top_wallets(wallets: list[str], min_score=0.55):
    return [w for w in wallets if wallet_score(w) >= min_score]


def get_best_wallet(wallets: list[str]):
    best = None
    best_score = 0

    for w in wallets:
        s = wallet_score(w)
        if s > best_score:
            best_score = s
            best = w

    return best


# =============================
# TOKEN ALPHA（🔥核心）
# =============================
def get_token_wallet_alpha(mint: str) -> dict:
    wallets = list(token_wallet_map.get(mint, set()))

    if not wallets:
        return {
            "count": 0,
            "top_count": 0,
            "best_wallet": None,
            "best_score": 0.0,
            "avg_score": 0.0,
            "cluster_score": 0.0,
        }

    scores = [wallet_score(w) for w in wallets]

    top_wallets = get_top_wallets(wallets)
    best_wallet = get_best_wallet(wallets)

    best_score = wallet_score(best_wallet) if best_wallet else 0.0
    avg_score = sum(scores) / len(scores) if scores else 0.0
    c_score = cluster_score(wallets)

    return {
        "count": len(wallets),
        "top_count": len(top_wallets),
        "best_wallet": best_wallet,
        "best_score": round(best_score, 4),
        "avg_score": round(avg_score, 4),
        "cluster_score": round(c_score, 4),
    }

from collections import defaultdict
import time

# wallet -> stats
wallet_stats = defaultdict(lambda: {
    "wins": 0,
    "losses": 0,
    "pnl": 0.0,
    "last_seen": 0,
})

# token -> wallets
token_wallet_map = defaultdict(set)


def record_wallet_trade(wallet: str, mint: str, pnl: float):
    """
    記錄某 wallet 在某 token 的表現
    """
    if not wallet:
        return

    s = wallet_stats[wallet]

    if pnl >= 0:
        s["wins"] += 1
    else:
        s["losses"] += 1

    s["pnl"] += pnl
    s["last_seen"] = time.time()

    token_wallet_map[mint].add(wallet)


def wallet_score(wallet: str) -> float:
    s = wallet_stats.get(wallet)
    if not s:
        return 0.0

    total = s["wins"] + s["losses"]
    if total == 0:
        return 0.0

    win_rate = s["wins"] / total
    avg_pnl = s["pnl"] / total

    score = (win_rate * 0.6) + (avg_pnl * 0.4)

    return max(min(score, 1.0), 0.0)


def get_top_wallets(limit=50):
    ranked = sorted(wallet_stats.items(), key=lambda x: wallet_score(x[0]), reverse=True)
    return [w for w, _ in ranked[:limit]]


def get_token_smart_score(mint: str) -> float:
    wallets = token_wallet_map.get(mint, [])
    if not wallets:
        return 0.0

    scores = [wallet_score(w) for w in wallets]

    if not scores:
        return 0.0

    return round(sum(scores) / len(scores), 4)

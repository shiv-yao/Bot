from collections import defaultdict
import time

wallet_stats = defaultdict(lambda: {
    "wins": 0,
    "losses": 0,
    "pnl": 0.0,
    "trades": 0,
    "recent_pnls": [],
    "last_seen": 0,
})

token_wallet_map = defaultdict(set)
wallet_links = defaultdict(lambda: defaultdict(int))
wallet_blacklist = set()


def record_wallet_result(wallet: str, pnl: float):
    if not wallet:
        wallet = "BOOTSTRAP"

    row = wallet_stats[wallet]
    row["trades"] += 1
    row["pnl"] += float(pnl)
    row["last_seen"] = time.time()

    if pnl >= 0:
        row["wins"] += 1
    else:
        row["losses"] += 1

    row["recent_pnls"].append(float(pnl))
    row["recent_pnls"] = row["recent_pnls"][-20:]

    if row["trades"] >= 10:
        win_rate = row["wins"] / row["trades"]
        avg = row["pnl"] / row["trades"]

        if win_rate < 0.2 and avg < -0.01:
            wallet_blacklist.add(wallet)


def track_token_wallets(mint: str, wallets: list[str]):
    if not mint or not wallets:
        return

    uniq = list(set(wallets))

    for w in uniq:
        token_wallet_map[mint].add(w)

    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            wallet_links[uniq[i]][uniq[j]] += 1
            wallet_links[uniq[j]][uniq[i]] += 1


# 舊版相容名稱
def record_token_wallets(mint: str, wallets: list[str]):
    track_token_wallets(mint, wallets)


def wallet_score(wallet: str) -> float:
    if wallet in wallet_blacklist:
        return 0.0

    row = wallet_stats.get(wallet)
    if not row:
        return 0.0

    trades = row["trades"]
    if trades < 3:
        return 0.0

    win_rate = row["wins"] / trades
    avg_pnl = row["pnl"] / trades

    recent = row["recent_pnls"]
    recent_avg = sum(recent) / len(recent) if recent else 0.0

    score = (
        win_rate * 0.5 +
        (avg_pnl * 6) * 0.3 +
        (recent_avg * 4) * 0.2
    )

    return max(min(score, 1.0), 0.0)


def cluster_score(wallets: list[str]) -> float:
    if len(wallets) <= 1:
        return 0.0

    links = 0
    pairs = 0

    for i in range(len(wallets)):
        for j in range(i + 1, len(wallets)):
            pairs += 1
            links += wallet_links[wallets[i]].get(wallets[j], 0)

    if pairs == 0:
        return 0.0

    return min((links / pairs) / 3.0, 1.0)


def get_top_wallets(wallets: list[str], min_score=0.55):
    return [w for w in wallets if wallet_score(w) >= min_score]


def get_best_wallet(wallets: list[str]):
    if not wallets:
        return None
    return max(wallets, key=wallet_score)


def get_token_wallet_alpha(mint: str):
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

    avg_score = sum(scores) / len(scores) if scores else 0.0
    best_score = wallet_score(best_wallet) if best_wallet else 0.0
    c_score = cluster_score(wallets)

    return {
        "count": len(wallets),
        "top_count": len(top_wallets),
        "best_wallet": best_wallet,
        "best_score": round(best_score, 4),
        "avg_score": round(avg_score, 4),
        "cluster_score": round(c_score, 4),
    }

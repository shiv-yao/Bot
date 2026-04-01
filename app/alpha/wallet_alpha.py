from collections import defaultdict
import time

wallet_stats = defaultdict(lambda: {
    "wins": 0,
    "losses": 0,
    "pnl": 0.0,
    "trades": 0,
    "recent": [],
    "last_seen": 0,
    "disabled": False,
})

token_wallet_map = defaultdict(set)
wallet_links = defaultdict(lambda: defaultdict(int))

BLACKLIST = set()


def record_wallet_result(wallet: str, pnl: float):
    if not wallet:
        wallet = "BOOTSTRAP"

    row = wallet_stats[wallet]

    row["trades"] += 1
    row["pnl"] += float(pnl)
    row["last_seen"] = time.time()

    if pnl > 0:
        row["wins"] += 1
    else:
        row["losses"] += 1

    row["recent"].append(float(pnl))
    row["recent"] = row["recent"][-20:]

    if row["trades"] >= 10:
        win_rate = row["wins"] / row["trades"]
        avg = row["pnl"] / row["trades"]

        if win_rate < 0.25 and avg < -0.01:
            row["disabled"] = True
            BLACKLIST.add(wallet)


def record_token_wallets(mint: str, wallets: list[str]):
    if not wallets:
        return

    uniq = list(set(wallets))

    for w in uniq:
        token_wallet_map[mint].add(w)

    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            a, b = uniq[i], uniq[j]
            wallet_links[a][b] += 1
            wallet_links[b][a] += 1


def get_wallet_score(wallet: str) -> float:
    if not wallet:
        return 0.0

    if wallet in BLACKLIST:
        return 0.0

    row = wallet_stats.get(wallet)
    if not row:
        return 0.0

    if row["disabled"]:
        return 0.0

    trades = row["trades"]
    if trades < 3:
        return 0.01

    win_rate = row["wins"] / max(trades, 1)
    avg = row["pnl"] / max(trades, 1)

    recent = row["recent"]
    recent_avg = sum(recent) / len(recent) if recent else 0.0

    score = 0.0
    score += win_rate * 0.4
    score += max(0.0, avg) * 0.4
    score += max(0.0, recent_avg) * 0.2

    return min(score, 1.0)


def get_top_wallets(wallets: list[str], min_score=0.35):
    return [w for w in wallets if get_wallet_score(w) >= min_score]


def get_best_wallet(wallets: list[str]):
    if not wallets:
        return None
    return max(wallets, key=get_wallet_score)


def get_token_wallet_alpha(mint: str):
    wallets = list(token_wallet_map.get(mint, []))

    if not wallets:
        return {
            "count": 0,
            "top_count": 0,
            "best_wallet": None,
            "best_score": 0.0,
            "avg_score": 0.0,
            "cluster_score": 0.0,
            "copy_signal": 0,
        }

    scores = [get_wallet_score(w) for w in wallets]

    avg = sum(scores) / len(scores) if scores else 0.0
    best_wallet = get_best_wallet(wallets)
    best_score = get_wallet_score(best_wallet) if best_wallet else 0.0
    top_wallets = get_top_wallets(wallets, min_score=0.35)

    cluster = 0.0
    for w in wallets:
        cluster += sum(wallet_links[w].values())
    cluster = min(cluster * 0.01, 0.3)

    strong = [s for s in scores if s > 0.3]
    copy_signal = min(len(strong) * 0.05, 0.3)

    return {
        "count": len(wallets),
        "top_count": len(top_wallets),
        "best_wallet": best_wallet,
        "best_score": round(best_score, 4),
        "avg_score": round(avg, 4),
        "cluster_score": round(cluster, 4),
        "copy_signal": round(copy_signal, 4),
    }

from collections import defaultdict
import time

# wallet -> stats
wallet_stats = defaultdict(lambda: {
    "wins": 0,
    "losses": 0,
    "pnl": 0.0,
    "trades": 0,
    "last_seen": 0.0,
    "recent_pnls": [],
})

# mint -> wallets seen on this token
token_wallet_map = defaultdict(set)

# wallet co-appearance graph
wallet_links = defaultdict(lambda: defaultdict(int))


def record_wallet_result(wallet: str, pnl: float):
    if not wallet:
        return

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


def track_token_wallets(mint: str, wallets: list[str]):
    if not mint or not wallets:
        return

    uniq = []
    seen = set()
    for w in wallets:
        if w and w not in seen:
            uniq.append(w)
            seen.add(w)

    for w in uniq:
        token_wallet_map[mint].add(w)

    # co-appearance links
    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            a = uniq[i]
            b = uniq[j]
            wallet_links[a][b] += 1
            wallet_links[b][a] += 1


# 兼容舊名字
record_token_wallets = track_token_wallets


def recent_form_score(wallet: str) -> float:
    row = wallet_stats.get(wallet)
    if not row:
        return 0.0

    arr = row.get("recent_pnls", [])
    if not arr:
        return 0.0

    avg_recent = sum(arr) / len(arr)
    # 5% 當飽和值
    return max(min(avg_recent / 0.05, 1.0), -1.0)


def wallet_score(wallet: str) -> float:
    row = wallet_stats.get(wallet)
    if not row:
        return 0.0

    trades = row["trades"]
    if trades < 3:
        return 0.0

    win_rate = row["wins"] / max(trades, 1)
    avg_pnl = row["pnl"] / max(trades, 1)
    form = recent_form_score(wallet)

    # 勝率 > 平均PnL > 近期狀態
    score = (
        win_rate * 0.50
        + (avg_pnl * 6.0) * 0.30
        + form * 0.20
    )

    return round(max(min(score, 1.0), 0.0), 4)


def cluster_score(wallets: list[str]) -> float:
    if not wallets or len(wallets) <= 1:
        return 0.0

    uniq = []
    seen = set()
    for w in wallets:
        if w and w not in seen:
            uniq.append(w)
            seen.add(w)

    pairs = 0
    link_sum = 0

    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            pairs += 1
            link_sum += wallet_links[uniq[i]].get(uniq[j], 0)

    if pairs == 0:
        return 0.0

    avg_link = link_sum / pairs
    # 平均共同出現 3 次視為強 cluster
    return round(max(min(avg_link / 3.0, 1.0), 0.0), 4)


def get_top_wallets(wallets: list[str], min_score: float = 0.55) -> list[str]:
    if not wallets:
        return []

    ranked = sorted(wallets, key=wallet_score, reverse=True)
    return [w for w in ranked if wallet_score(w) >= min_score]


def get_best_wallet(wallets: list[str]) -> str | None:
    if not wallets:
        return None

    ranked = sorted(wallets, key=wallet_score, reverse=True)
    return ranked[0] if ranked else None


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
    top_wallets = get_top_wallets(wallets, min_score=0.55)
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
        "cluster_score": c_score,
    }

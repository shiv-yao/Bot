from collections import defaultdict
import time

wallet_stats = defaultdict(lambda: {
    "wins": 0,
    "losses": 0,
    "pnl": 0.0,
    "last_seen": 0.0,
    "recent_pnls": [],
})

token_wallet_map = defaultdict(set)
wallet_links = defaultdict(lambda: defaultdict(int))


def record_wallet_result(wallet: str, pnl: float):
    if not wallet:
        return

    s = wallet_stats[wallet]

    if pnl >= 0:
        s["wins"] += 1
    else:
        s["losses"] += 1

    s["pnl"] += float(pnl)
    s["last_seen"] = time.time()

    s["recent_pnls"].append(float(pnl))
    s["recent_pnls"] = s["recent_pnls"][-20:]


def record_token_wallets(mint: str, wallets: list[str]):
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

    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            a = uniq[i]
            b = uniq[j]
            wallet_links[a][b] += 1
            wallet_links[b][a] += 1


def recent_form_score(wallet: str) -> float:
    s = wallet_stats.get(wallet)
    if not s:
        return 0.0

    arr = s.get("recent_pnls", [])
    if not arr:
        return 0.0

    avg_recent = sum(arr) / len(arr)
    return max(min(avg_recent / 0.05, 1.0), -1.0)


def wallet_score(wallet: str) -> float:
    s = wallet_stats.get(wallet)
    if not s:
        return 0.0

    total = s["wins"] + s["losses"]
    if total == 0:
        return 0.0

    win_rate = s["wins"] / total
    avg_pnl = s["pnl"] / total
    form = recent_form_score(wallet)

    score = (win_rate * 0.5) + (avg_pnl * 6.0 * 0.3) + (form * 0.2)
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

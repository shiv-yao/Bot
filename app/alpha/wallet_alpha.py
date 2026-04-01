from collections import defaultdict

# wallet -> 表現
wallet_stats = defaultdict(lambda: {
    "wins": 0,
    "losses": 0,
    "pnl": 0.0,
})


def record_wallet_result(wallet: str, pnl: float):
    if not wallet:
        return

    if pnl >= 0:
        wallet_stats[wallet]["wins"] += 1
    else:
        wallet_stats[wallet]["losses"] += 1

    wallet_stats[wallet]["pnl"] += pnl


def get_wallet_score(wallet: str) -> float:
    s = wallet_stats.get(wallet)
    if not s:
        return 0.0

    total = s["wins"] + s["losses"]
    if total == 0:
        return 0.0

    win_rate = s["wins"] / total
    pnl_score = s["pnl"]

    score = (win_rate * 0.7) + (pnl_score * 0.3)
    return round(max(min(score, 1.0), 0.0), 4)


def get_top_wallets(wallets: list[str], min_score=0.55) -> list[str]:
    result = []
    for w in wallets:
        if get_wallet_score(w) >= min_score:
            result.append(w)
    return result


def get_best_wallet(wallets: list[str]) -> str | None:
    best = None
    best_score = 0

    for w in wallets:
        s = get_wallet_score(w)
        if s > best_score:
            best_score = s
            best = w

    return best

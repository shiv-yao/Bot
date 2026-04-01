from collections import defaultdict
import time

# =========================
# GLOBAL STORAGE
# =========================

# wallet -> performance stats
wallet_stats = defaultdict(lambda: {
    "wins": 0,
    "losses": 0,
    "pnl": 0.0,
    "trades": 0,
    "recent_pnls": [],
    "last_seen": 0.0,
})

# mint -> wallets seen on this token
token_wallet_map = defaultdict(set)

# wallet graph / co-appearance
wallet_links = defaultdict(lambda: defaultdict(int))

# auto blacklist
wallet_blacklist = set()


# =========================
# RECORD WALLET RESULT
# =========================
def record_wallet_result(wallet: str, pnl: float):
    if not wallet:
        wallet = "BOOTSTRAP_WALLET"

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

    # 自動黑名單：夠多交易後仍明顯爛
    if row["trades"] >= 8:
        win_rate = row["wins"] / max(row["trades"], 1)
        avg_pnl = row["pnl"] / max(row["trades"], 1)

        if win_rate < 0.25 and avg_pnl < -0.01:
            wallet_blacklist.add(wallet)


# =========================
# TRACK TOKEN WALLETS
# =========================
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

    # 建 graph
    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            a = uniq[i]
            b = uniq[j]
            wallet_links[a][b] += 1
            wallet_links[b][a] += 1


# 舊名稱相容
def record_token_wallets(mint: str, wallets: list[str]):
    track_token_wallets(mint, wallets)


# =========================
# WALLET SCORING
# =========================
def wallet_score(wallet: str) -> float:
    if not wallet:
        return 0.0

    if wallet in wallet_blacklist:
        return 0.0

    row = wallet_stats.get(wallet)
    if not row:
        return 0.0

    trades = row["trades"]
    if trades < 3:
        return 0.0

    wins = row["wins"]
    pnl = row["pnl"]

    win_rate = wins / max(trades, 1)
    avg_pnl = pnl / max(trades, 1)

    recent = row["recent_pnls"]
    recent_avg = sum(recent) / len(recent) if recent else 0.0

    score = (
        win_rate * 0.40
        + (avg_pnl * 6.0) * 0.40
        + (recent_avg * 4.0) * 0.20
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

    links = 0
    pairs = 0

    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            pairs += 1
            links += wallet_links[uniq[i]].get(uniq[j], 0)

    if pairs == 0:
        return 0.0

    return round(min((links / pairs) / 3.0, 1.0), 4)


def get_top_wallets(wallets: list[str], min_score=0.35):
    return [w for w in wallets if wallet_score(w) >= min_score]


def get_best_wallet(wallets: list[str]):
    if not wallets:
        return None
    return max(wallets, key=wallet_score)


# =========================
# TOKEN WALLET ALPHA (V6)
# =========================
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
            "copy_signal": 0,
        }

    scores = [wallet_score(w) for w in wallets]
    top_wallets = get_top_wallets(wallets, min_score=0.35)
    best_wallet = get_best_wallet(wallets)

    avg_score = sum(scores) / len(scores) if scores else 0.0
    best_score = wallet_score(best_wallet) if best_wallet else 0.0
    c_score = cluster_score(wallets)

    # copy signal
    copy_signal = 0
    if best_score > 0.55:
        copy_signal += 1
    if len(top_wallets) >= 2:
        copy_signal += 1
    if c_score > 0.30:
        copy_signal += 1

    return {
        "count": len(wallets),
        "top_count": len(top_wallets),
        "best_wallet": best_wallet,
        "best_score": round(best_score, 4),
        "avg_score": round(avg_score, 4),
        "cluster_score": round(c_score, 4),
        "copy_signal": copy_signal,
    }

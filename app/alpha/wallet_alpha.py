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
    row["pnl"] += pnl
    row["last_seen"] = time.time()

    if pnl > 0:
        row["wins"] += 1
    else:
        row["losses"] += 1

    row["recent"].append(pnl)
    row["recent"] = row["recent"][-20:]

    # 爛 wallet 自動封殺
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

    # cluster graph
    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            a, b = uniq[i], uniq[j]
            wallet_links[a][b] += 1
            wallet_links[b][a] += 1


def get_wallet_score(wallet: str) -> float:
    if wallet in BLACKLIST:
        return 0

    row = wallet_stats.get(wallet)
    if not row or row["trades"] < 3:
        return 0.01  # bootstrap

    win_rate = row["wins"] / row["trades"]
    avg = row["pnl"] / row["trades"]

    score = 0
    score += win_rate * 0.4
    score += max(0, avg) * 0.6

    return min(score, 1.0)


def get_token_wallet_alpha(mint: str):
    wallets = list(token_wallet_map.get(mint, []))

    if not wallets:
        return 0, 0, 0, 0

    scores = [get_wallet_score(w) for w in wallets]

    avg = sum(scores) / len(scores)
    best = max(scores)

    # cluster
    cluster = 0
    for w in wallets:
        cluster += sum(wallet_links[w].values())

    cluster = min(cluster * 0.01, 0.3)

    # copy signal
    strong = [s for s in scores if s > 0.3]
    copy_signal = min(len(strong) * 0.05, 0.3)

    return avg, best, cluster, copy_signal

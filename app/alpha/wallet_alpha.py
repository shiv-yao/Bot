from collections import defaultdict

wallet_history = defaultdict(list)
token_wallets = defaultdict(list)

def record_wallet(wallet, pnl):
    wallet_history[wallet].append(pnl)

def record_token_wallets(mint, wallets):
    token_wallets[mint] = wallets[:10]

def score_wallet(wallet):
    trades = wallet_history.get(wallet, [])

    if len(trades) < 3:
        return 0.05

    win = sum(1 for x in trades if x > 0)
    winrate = win / len(trades)
    avg = sum(trades) / len(trades)

    return min(1.0, winrate * 0.7 + max(avg, 0) * 5)

def get_wallet_alpha(mint):
    wallets = token_wallets.get(mint, [])

    if not wallets:
        return None

    scores = [(w, score_wallet(w)) for w in wallets]

    best_wallet, best = max(scores, key=lambda x: x[1])
    avg = sum(s for _, s in scores) / len(scores)

    strong = [s for _, s in scores if s > 0.2]
    cluster = len(strong) / len(scores)

    return {
        "avg": avg,
        "best": best,
        "cluster": cluster,
        "wallet": best_wallet
    }

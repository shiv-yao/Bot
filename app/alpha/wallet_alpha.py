from collections import defaultdict

wallet_trades = defaultdict(list)
token_wallets = defaultdict(list)

MIN_TRADES = 3


def record_wallet_result(wallet, pnl):
    if not wallet:
        wallet = "BOOTSTRAP_WALLET"

    wallet_trades[wallet].append(float(pnl))

    if len(wallet_trades[wallet]) > 50:
        wallet_trades[wallet] = wallet_trades[wallet][-50:]


def record_token_wallets(mint, wallets):
    if not wallets:
        return

    token_wallets[mint] = list(dict.fromkeys(wallets))[:10]


def get_wallet_score(wallet):
    trades = wallet_trades.get(wallet, [])

    if len(trades) < MIN_TRADES:
        return 0.05

    wins = sum(1 for x in trades if x > 0)
    winrate = wins / len(trades)
    avg = sum(trades) / len(trades)

    return min(1.0, winrate * 0.7 + max(avg, 0) * 5)


def get_top_wallets(wallets, min_score=0.35):
    return [w for w in wallets if get_wallet_score(w) >= min_score]


def get_best_wallet(wallets):
    if not wallets:
        return None
    return max(wallets, key=get_wallet_score)


def get_token_wallet_alpha(mint):
    wallets = token_wallets.get(mint, [])

    if not wallets:
        return {
            "avg": 0.0,
            "best": 0.0,
            "cluster": 0.0,
            "count": 0,
            "top_wallet": None,
            "copy_signal": 0,
        }

    scored = [(w, get_wallet_score(w)) for w in wallets]

    best_wallet, best_score = max(scored, key=lambda x: x[1])
    avg = sum(s for _, s in scored) / len(scored)

    strong = [s for _, s in scored if s > 0.2]
    cluster = len(strong) / len(scored) if scored else 0.0

    copy_signal = 1 if best_score > 0.5 else 0

    return {
        "avg": avg,
        "best": best_score,
        "cluster": cluster,
        "count": len(wallets),
        "top_wallet": best_wallet,
        "copy_signal": copy_signal,
    }

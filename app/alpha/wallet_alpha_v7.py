from collections import defaultdict

wallet_trades = defaultdict(list)
token_wallets = defaultdict(list)

MIN_TRADES = 3


def record_wallet_trade(wallet: str, pnl: float):
    if not wallet:
        wallet = "BOOTSTRAP_WALLET"

    wallet_trades[wallet].append(float(pnl))

    if len(wallet_trades[wallet]) > 50:
        wallet_trades[wallet] = wallet_trades[wallet][-50:]


def record_token_wallets(mint: str, wallets: list[str]):
    if not wallets:
        return

    # 保留順序去重，取 early wallets
    uniq = list(dict.fromkeys(wallets))
    token_wallets[mint] = uniq[:10]


def wallet_score(wallet: str) -> float:
    trades = wallet_trades.get(wallet, [])

    if len(trades) < MIN_TRADES:
        return 0.05

    wins = sum(1 for x in trades if x > 0)
    winrate = wins / len(trades)
    avg = sum(trades) / len(trades)

    return min(1.0, winrate * 0.7 + max(avg, 0.0) * 5.0)


def get_wallet_alpha(mint: str):
    wallets = token_wallets.get(mint, [])

    if not wallets:
        return None

    scored = [(w, wallet_score(w)) for w in wallets]

    best_wallet, best_score = max(scored, key=lambda x: x[1])
    avg = sum(s for _, s in scored) / len(scored)

    strong = [s for _, s in scored if s > 0.2]
    cluster = len(strong) / len(scored) if scored else 0.0

    copy_signal = 1 if best_score > 0.5 else 0

    return {
        "avg": avg,
        "best": best_score,
        "cluster": cluster,
        "top_wallet": best_wallet,
        "copy_signal": copy_signal,
        "count": len(wallets),
    }

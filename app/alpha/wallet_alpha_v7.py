from collections import defaultdict

token_wallets = defaultdict(list)

def record_token_wallets(mint, wallets):
    if not wallets:
        return

    uniq = list(dict.fromkeys(wallets))
    token_wallets[mint] = uniq[:10]

def get_wallet_alpha(mint):
    ws = token_wallets.get(mint, [])
    if not ws:
        return None

    scores = [0.1 + i*0.05 for i in range(len(ws))]

    avg = sum(scores) / len(scores)
    best = max(scores)
    cluster = len([s for s in scores if s > 0.2]) / len(scores)

    return {
        "avg": avg,
        "best": best,
        "cluster": cluster,
        "copy_signal": 1 if best > 0.5 else 0,
        "top_wallet": ws[0],
        "count": len(ws),
    }

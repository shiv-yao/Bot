import time
from collections import defaultdict

# wallet → 最近行為
wallet_trades = defaultdict(list)

# token → 哪些 wallet 買過
token_wallets = defaultdict(set)


def record_wallet_trade(wallet: str, mint: str, side: str, amount: float):
    wallet_trades[wallet].append({
        "mint": mint,
        "side": side,
        "amount": amount,
        "time": time.time(),
    })

    if side == "buy":
        token_wallets[mint].add(wallet)


def get_wallets_for_token(mint: str):
    return list(token_wallets.get(mint, []))


def get_wallet_history(wallet: str):
    return wallet_trades.get(wallet, [])

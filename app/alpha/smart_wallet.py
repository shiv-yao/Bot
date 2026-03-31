import random

SMART_WALLETS = {}

def rank_wallet(wallet):
    # mock → 之後接真資料
    score = random.random()
    SMART_WALLETS[wallet] = score
    return score


def flow_score():
    if not SMART_WALLETS:
        return 0
    return sum(SMART_WALLETS.values()) / len(SMART_WALLETS)

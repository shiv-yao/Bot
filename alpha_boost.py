# alpha_boost.py

import random

async def wallet_graph_alpha(candidates):
    if not candidates:
        return None, 0

    mint = random.choice(list(candidates))
    score = random.uniform(800, 1200)

    return mint, score


async def insider_early_alpha(candidates):
    if not candidates:
        return None, 0

    mint = random.choice(list(candidates))
    score = random.uniform(900, 1300)

    return mint, score


async def smart_flow_alpha(candidates):
    if not candidates:
        return None, 0

    mint = random.choice(list(candidates))
    score = random.uniform(700, 1100)

    return mint, score


async def momentum_accel_alpha(candidates):
    if not candidates:
        return None, 0

    mint = random.choice(list(candidates))
    score = random.uniform(600, 900)

    return mint, score

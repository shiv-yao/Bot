import asyncio
import random
import time


def _base_price(mint: str) -> float:
    seed = sum(ord(c) for c in mint[:8])
    return 1.0 + (seed % 100) / 1000.0


async def get_price(token: dict | str) -> float:
    await asyncio.sleep(0)

    mint = token["mint"] if isinstance(token, dict) else token
    base = _base_price(mint)

    # 模擬小幅波動，讓買賣循環能運作
    phase = time.time() % 20
    drift = 0.0

    if phase < 5:
        drift = 0.004 * phase
    elif phase < 10:
        drift = 0.02 - 0.003 * (phase - 5)
    elif phase < 15:
        drift = 0.005 - 0.004 * (phase - 10)
    else:
        drift = -0.015 + 0.003 * (phase - 15)

    drift += random.uniform(-0.004, 0.006)

    return max(0.0001, base * (1 + drift))

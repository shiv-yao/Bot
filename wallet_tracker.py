import time
from collections import defaultdict
import httpx

HTTP = httpx.AsyncClient(timeout=10)

WALLET_TOKEN_SCORE = defaultdict(float)
TOKEN_LAST_SEEN = defaultdict(float)

def now():
    return time.time()

def wallet_score(mint):
    base = WALLET_TOKEN_SCORE.get(mint, 0.0)

    if base == 0:
        return 1.0   # 🔥 bootstrap

    age = now() - TOKEN_LAST_SEEN.get(mint, 0)
    decay = max(0.2, 1 - age / 3600)
    return base * decay

async def wallet_tracker_loop(rpc, wallets, callback):
    while True:
        try:
            for w in wallets:
                # 模擬 wallet 行為（簡化）
                mint = None
                if mint:
                    WALLET_TOKEN_SCORE[mint] += 1
                    TOKEN_LAST_SEEN[mint] = now()
                    await callback(mint)
        except:
            pass

        await asyncio.sleep(5)

async def discover_active_wallets_from_candidates(rpc, mints):
    # 簡化版（避免 crash）
    return []

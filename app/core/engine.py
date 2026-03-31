import asyncio
import time
from app.state import engine
from app.data.market import get_candidates
from app.execution.jupiter import safe_jupiter_order

COOLDOWN = 15
MAX_POSITIONS = 2


def log(msg):
    print(msg)
    engine.logs.append(msg)


def score_token(t):
    # 🔥 簡單但有效
    momentum = t.get("momentum", 0)
    volume = t.get("volume", 0)

    score = momentum * 0.7 + volume * 0.000001
    return score


async def process_token(t):
    m = t["mint"]

    # 🔥 避免持倉重複
    if m in engine.positions:
        return

    # 🔥 cooldown
    if time.time() - engine.last_trade.get(m, 0) < COOLDOWN:
        log(f"COOLDOWN {m[:6]}")
        return

    score = score_token(t)
    thr = engine.threshold

    log(f"SCORE {m[:6]} score={score:.4f} thr={thr:.4f}")

    if score < thr:
        engine.stats["rejected"] += 1
        return

    if len(engine.positions) >= MAX_POSITIONS:
        log("MAX_POSITIONS")
        return

    # 🔥 嘗試下單
    result = await safe_jupiter_order(m)

    if result:
        engine.positions.append(m)
        engine.last_trade[m] = time.time()
        log(f"EXEC SUCCESS {m[:6]}")
    else:
        log(f"EXEC FAIL {m[:6]}")


async def run_engine():
    while engine.running:
        try:
            tokens = await get_candidates()

            # 🔥 排序只打前2
            ranked = sorted(tokens, key=score_token, reverse=True)[:2]

            for t in ranked:
                await process_token(t)

            await asyncio.sleep(2)

        except Exception as e:
            log(f"ENGINE_ERR {e}")
            await asyncio.sleep(3)

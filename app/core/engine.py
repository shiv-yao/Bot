import asyncio
import time
from collections import defaultdict
from state import engine
from app.execution.jupiter import safe_jupiter_order

# ================= CONFIG =================

CONFIG = {
    "BASE_SIZE": 0.0015,
    "MAX_POSITIONS": 4,

    # 🔥 關鍵：開始會賺
    "TAKE_PROFIT": 0.08,
    "STOP_LOSS": -0.06,
    "TRAILING_STOP": -0.025,

    # 🔥 新增
    "MAX_HOLD_SEC": 60,
    "MIN_SCORE": 0.015,
}

# ================= STATE =================

COOLDOWN = defaultdict(float)

# ================= MOCK DATA (可換真API) =================

async def scan_tokens():
    # 你之後可以換 dex / pump.fun API
    return [
        {"mint": "A", "momentum": 0.02, "flow": 0.01},
        {"mint": "B", "momentum": 0.018, "flow": 0.005},
        {"mint": "C", "momentum": 0.01, "flow": 0.0},
    ]

# ================= SCORE =================

def score_token(t):
    return t["momentum"] * 0.7 + t["flow"] * 0.3

# ================= BUY =================

async def try_buy(t):
    mint = t["mint"]

    if len(engine.positions) >= CONFIG["MAX_POSITIONS"]:
        log("MAX_POSITIONS")
        return

    if time.time() - COOLDOWN[mint] < 30:
        log(f"COOLDOWN {mint}")
        return

    score = score_token(t)

    if score < CONFIG["MIN_SCORE"]:
        return

    log(f"SCORE {mint} score={score:.4f}")

    # ================= 下單 =================
    order = await safe_jupiter_order(mint, CONFIG["BASE_SIZE"])

    if not order:
        return

    out = order.get("outAmount", 0)

    engine.positions.append({
        "mint": mint,
        "entry_out": out,
        "size": CONFIG["BASE_SIZE"],
        "peak": out,
        "time": time.time(),
    })

    COOLDOWN[mint] = time.time()

    log(f"BUY {mint} out={out}")

# ================= POSITION MGMT =================

async def manage_positions():
    now = time.time()

    for pos in list(engine.positions):
        mint = pos["mint"]

        # 模擬價格（之後可接 dex price）
        current = pos["entry_out"] * (1 + (0.01 - 0.02 * (time.time() % 2)))

        pnl = (current - pos["entry_out"]) / pos["entry_out"]

        log(f"PNL {mint} {pnl:.4f}")

        # 更新 peak
        if current > pos["peak"]:
            pos["peak"] = current

        drawdown = (current - pos["peak"]) / pos["peak"]

        # ================= SELL LOGIC =================

        # 🟢 TP
        if pnl >= CONFIG["TAKE_PROFIT"]:
            log(f"💰 TP {mint}")
            engine.positions.remove(pos)
            continue

        # 🔴 SL
        if pnl <= CONFIG["STOP_LOSS"]:
            log(f"🛑 SL {mint}")
            engine.positions.remove(pos)
            continue

        # 🔵 TRAIL
        if drawdown <= CONFIG["TRAILING_STOP"]:
            log(f"🔵 TRAIL {mint}")
            engine.positions.remove(pos)
            continue

        # ⏱ TIME EXIT（超重要）
        if now - pos["time"] > CONFIG["MAX_HOLD_SEC"]:
            log(f"⏱ TIME_EXIT {mint}")
            engine.positions.remove(pos)
            continue

# ================= LOOP =================

async def main_loop():
    while True:
        try:
            tokens = await scan_tokens()

            # ⭐ 排名（關鍵）
            ranked = sorted(tokens, key=score_token, reverse=True)

            # 只打前2名
            for t in ranked[:2]:
                await try_buy(t)

            await manage_positions()

        except Exception as e:
            log(f"ERROR {e}")

        await asyncio.sleep(2)

# ================= LOG =================

def log(msg):
    print(msg)
    engine.logs.append(msg)

    if len(engine.logs) > 200:
        engine.logs.pop(0)

import asyncio
import random
import time
from collections import defaultdict

from app.state import engine

# ===== CONFIG =====
BASE_SIZE = 270000
MAX_POSITIONS = 4
ENTRY_THRESHOLD = 0.015

TAKE_PROFIT = 0.08
STOP_LOSS = -0.06
TRAILING_STOP = -0.025
MAX_HOLD_SEC = 60

TOKEN_COOLDOWN = 15
TOP_N = 4

LAST_TRADE = defaultdict(float)


def log(msg: str):
    msg = str(msg)
    print(msg)
    engine.logs.append(msg)
    engine.logs = engine.logs[-200:]


# ===== MOCK SCANNER =====
async def fetch_candidates():
    # 先用穩定 mock 資料，確保整體流程正常
    base = [
        {"mint": "8F8FLuwv7iL26ecsQ1yXmYKJ6us6Y55QEpJDMFk11Wau", "momentum": 0.020},
        {"mint": "sosd5Q3DutGxMEaukBDmkPgsapMQz59jNjGWmhYcdTQ", "momentum": 0.018},
        {"mint": "SooEj828BSjtgTecBRkqBJ4oquc713yyFZqbCawawoN", "momentum": 0.017},
        {"mint": "sokhCSmzutMPPuNcxG1j6gYLowgiM8mswjJu8FBYm5r", "momentum": 0.020},
    ]
    await asyncio.sleep(0)
    return base[:TOP_N]


def score_token(item: dict) -> float:
    return float(item.get("momentum", 0.0))


def fake_buy_out(score: float) -> int:
    # 用分數模擬買到的 token 數量
    return int(200 + score * 3000)


def fake_mark_to_market(entry_out: int, age_sec: float) -> int:
    # 純流程驗證，不是真實市場價格
    phase = age_sec % 20
    drift = 0.0

    if phase < 6:
        drift = 0.02 * phase
    elif phase < 12:
        drift = 0.12 - 0.01 * (phase - 6)
    else:
        drift = 0.06 - 0.015 * (phase - 12)

    drift += random.uniform(-0.01, 0.01)
    return max(1, int(entry_out * (1 + drift)))


async def manage_positions():
    now = time.time()

    for pos in engine.positions[:]:
        try:
            mint = pos["mint"]
            current_out = fake_mark_to_market(pos["entry_out"], now - pos["time"])
            entry = pos["entry_out"]

            if current_out > pos["peak"]:
                pos["peak"] = current_out

            pnl = (current_out - entry) / max(entry, 1)
            drawdown = (current_out - pos["peak"]) / max(pos["peak"], 1)

            log(f"PNL {mint[:6]} {pnl:.4f}")

            if pnl >= TAKE_PROFIT:
                log(f"TP {mint[:6]}")
                engine.positions.remove(pos)
                engine.capital *= (1 + pnl)
                continue

            if pnl <= STOP_LOSS:
                log(f"SL {mint[:6]}")
                engine.positions.remove(pos)
                engine.capital *= (1 + pnl)
                continue

            if pos["peak"] > entry and drawdown <= TRAILING_STOP:
                log(f"TRAIL {mint[:6]}")
                engine.positions.remove(pos)
                engine.capital *= (1 + pnl)
                continue

            if now - pos["time"] > MAX_HOLD_SEC:
                log(f"TIME_EXIT {mint[:6]}")
                engine.positions.remove(pos)
                engine.capital *= (1 + pnl)
                continue

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"SELL_ERR {e}")


async def try_trade(item: dict):
    mint = item["mint"]
    now = time.time()

    if any(p["mint"] == mint for p in engine.positions):
        log(f"ALREADY_HELD {mint[:6]}")
        return

    if len(engine.positions) >= MAX_POSITIONS:
        log("MAX_POSITIONS")
        return

    if now - LAST_TRADE[mint] < TOKEN_COOLDOWN:
        log(f"COOLDOWN {mint[:6]}")
        return

    score = score_token(item)
    engine.stats["signals"] += 1
    engine.last_signal = f"{mint[:6]} score={score:.4f}"

    log(f"SCORE {mint[:6]} {score:.4f}")

    if score < ENTRY_THRESHOLD:
        engine.stats["rejected"] += 1
        log(f"REJECT {mint[:6]}")
        return

    out_amount = fake_buy_out(score)

    log(f"BUY {mint[:6]} size={BASE_SIZE} out={out_amount}")

    engine.positions.append({
        "mint": mint,
        "entry_out": out_amount,
        "size": BASE_SIZE,
        "peak": out_amount,
        "time": now,
    })

    LAST_TRADE[mint] = now
    engine.stats["executed"] += 1
    log(f"EXECUTED {mint[:6]}")


async def main_loop():
    log("ENGINE STARTED")

    while engine.running:
        try:
            await manage_positions()

            items = await fetch_candidates()
            for item in items:
                await try_trade(item)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"LOOP_ERR {e}")

        await asyncio.sleep(3)

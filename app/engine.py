import asyncio
import random
import time
from collections import defaultdict

from app.state import engine

# ===== CONFIG =====

BASE_SIZE = 270000
MAX_POSITIONS = 4
ENTRY_THRESHOLD = 0.015

# 🔥 關鍵修正
TAKE_PROFIT = 0.05      # 降低 → 會觸發
STOP_LOSS = -0.06
TRAILING_STOP = -0.025

MAX_HOLD_SEC = 20       # 🔥 強制出場
TOKEN_COOLDOWN = 10
TOP_N = 4

LAST_TRADE = defaultdict(float)


def log(msg: str):
    msg = str(msg)
    print(msg)
    engine.logs.append(msg)
    engine.logs = engine.logs[-200:]


# ===== MOCK SCANNER =====

async def fetch_candidates():
    base = [
        {"mint": "8F8FLuwv7iL26ecsQ1yXmYKJ6us6Y55QEpJDMFk11Wau", "momentum": 0.020},
        {"mint": "sosd5Q3DutGxMEaukBDmkPgsapMQz59jNjGWmhYcdTQ", "momentum": 0.018},
        {"mint": "SooEj828BSjtgTecBRkqBJ4oquc713yyFZqbCawawoN", "momentum": 0.017},
        {"mint": "sokhCSmzutMPPuNcxG1j6gYLowgiM8mswjJu8FBYm5r", "momentum": 0.020},
    ]
    await asyncio.sleep(0)
    return base[:TOP_N]


def score_token(item):
    return item["momentum"]


def fake_buy_out(score):
    return int(200 + score * 3000)


# ===== 🔥 修正：讓市場會動 =====
def fake_mark_to_market(entry_out, age_sec):
    phase = age_sec % 20

    if phase < 5:
        drift = 0.03 * phase
    elif phase < 10:
        drift = 0.15 - 0.02 * (phase - 5)
    else:
        drift = -0.02 * (phase - 10)

    # 🔥 大波動（關鍵）
    drift += random.uniform(-0.05, 0.08)

    return max(1, int(entry_out * (1 + drift)))


# ===== POSITION MANAGEMENT =====

async def manage_positions():
    now = time.time()

    for pos in engine.positions[:]:
        try:
            mint = pos["mint"]
            current = fake_mark_to_market(pos["entry_out"], now - pos["time"])

            entry = pos["entry_out"]

            if current > pos["peak"]:
                pos["peak"] = current

            pnl = (current - entry) / entry
            drawdown = (current - pos["peak"]) / pos["peak"]

            # 🔥 DEBUG（關鍵）
            log(f"CHECK {mint[:6]} pnl={pnl:.4f} dd={drawdown:.4f}")

            # ===== SELL LOGIC =====

            if pnl >= TAKE_PROFIT:
                log(f"💰 TP {mint[:6]}")
                engine.positions.remove(pos)
                engine.capital *= (1 + pnl)
                continue

            if pnl <= STOP_LOSS:
                log(f"🛑 SL {mint[:6]}")
                engine.positions.remove(pos)
                engine.capital *= (1 + pnl)
                continue

            if pos["peak"] > entry and drawdown <= TRAILING_STOP:
                log(f"🔵 TRAIL {mint[:6]}")
                engine.positions.remove(pos)
                engine.capital *= (1 + pnl)
                continue

            # 🔥 TIME EXIT（你之前卡死原因）
            if now - pos["time"] > MAX_HOLD_SEC:
                log(f"⏱ TIME_EXIT {mint[:6]}")
                engine.positions.remove(pos)
                engine.capital *= (1 + pnl)
                continue

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"SELL_ERR {e}")


# ===== BUY =====

async def try_trade(item):
    mint = item["mint"]
    now = time.time()

    if any(p["mint"] == mint for p in engine.positions):
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
        return

    out = fake_buy_out(score)

    log(f"BUY {mint[:6]} out={out}")

    engine.positions.append({
        "mint": mint,
        "entry_out": out,
        "size": BASE_SIZE,
        "peak": out,
        "time": now,
    })

    LAST_TRADE[mint] = now
    engine.stats["executed"] += 1


# ===== LOOP =====

async def main_loop():
    log("🚀 ENGINE STARTED")

    while engine.running:
        try:
            await manage_positions()

            tokens = await fetch_candidates()

            ranked = sorted(tokens, key=score_token, reverse=True)

            for t in ranked:
                await try_trade(t)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"LOOP_ERR {e}")

        await asyncio.sleep(2)

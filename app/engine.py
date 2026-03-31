import asyncio
import random
import time
from collections import defaultdict

from app.state import engine

# ===== CONFIG =====
BASE_SIZE = 270000
MAX_POSITIONS = 4
ENTRY_THRESHOLD = 0.015

# V7.2: 開始讓它真的會出場
TAKE_PROFIT = 0.02       # +2%
STOP_LOSS = -0.01        # -1%
TRAILING_STOP = -0.008   # 從 peak 回落 0.8%
MAX_HOLD_SEC = 20        # 最長持有 20 秒

TOKEN_COOLDOWN = 10
TOP_N = 4

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}


def log(msg: str):
    msg = str(msg)
    print(msg)
    engine.logs.append(msg)
    engine.logs = engine.logs[-200:]


# ===== MOCK SCANNER =====
async def fetch_candidates():
    # 先固定用 mock token，驗證系統買賣循環
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
    return int(200 + score * 3000)


def fake_mark_to_market(entry_out: int, age_sec: float) -> int:
    # V7.2: 故意讓價格有波動，好觸發 TP / SL / TRAIL
    phase = age_sec % 16

    if phase < 4:
        drift = 0.012 * phase
    elif phase < 8:
        drift = 0.05 - 0.01 * (phase - 4)
    elif phase < 12:
        drift = 0.01 - 0.015 * (phase - 8)
    else:
        drift = -0.05 + 0.008 * (phase - 12)

    drift += random.uniform(-0.012, 0.015)

    return max(1, int(entry_out * (1 + drift)))


async def get_price(mint: str, entry_out: int, age_sec: float) -> int:
    # 目前仍用 mock price，先讓買賣循環完整
    await asyncio.sleep(0)
    return fake_mark_to_market(entry_out, age_sec)


async def check_exit(pos: dict, current_out: int):
    entry = pos["entry_out"]
    peak = pos["peak"]

    pnl = (current_out - entry) / max(entry, 1)
    drawdown = (current_out - peak) / max(peak, 1)

    # 先記 debug
    log(f"CHECK {pos['mint'][:6]} pnl={pnl:.4f} dd={drawdown:.4f}")

    if pnl >= TAKE_PROFIT:
        return "TP", pnl

    if pnl <= STOP_LOSS:
        return "SL", pnl

    if peak > entry and drawdown <= TRAILING_STOP:
        return "TRAIL", pnl

    if time.time() - pos["time"] >= MAX_HOLD_SEC:
        return "TIME_EXIT", pnl

    return None, pnl


async def try_sell(pos: dict, current_out: int):
    reason, pnl = await check_exit(pos, current_out)
    if not reason:
        return False

    try:
        engine.positions.remove(pos)
    except ValueError:
        return False

    engine.capital *= (1 + pnl)

    log(
        f"SELL {pos['mint'][:6]} "
        f"{reason} out={current_out} pnl={pnl:.4f} capital={engine.capital:.4f}"
    )

    return True


async def manage_positions():
    now = time.time()

    for pos in list(engine.positions):
        try:
            mint = pos["mint"]
            age_sec = now - pos["time"]
            current_out = await get_price(mint, pos["entry_out"], age_sec)

            if current_out > pos["peak"]:
                pos["peak"] = current_out

            await try_sell(pos, current_out)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"SELL_ERR {e}")


async def try_trade(item: dict):
    mint = item["mint"]
    now = time.time()

    # 不重複持有同一個 token
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

    # 避免盤整太死的 token 一直進
    last_price = LAST_PRICE.get(mint)
    new_price = fake_buy_out(score)

    if last_price is not None:
        move = abs(new_price - last_price) / max(last_price, 1)
        if move < 0.003:
            log(f"FLAT_SKIP {mint[:6]}")
            return

    LAST_PRICE[mint] = new_price

    out_amount = new_price

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
            # 先處理賣出
            await manage_positions()

            # 再找新標的
            items = await fetch_candidates()
            ranked = sorted(items, key=score_token, reverse=True)

            for item in ranked:
                await try_trade(item)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"LOOP_ERR {e}")

        await asyncio.sleep(2)

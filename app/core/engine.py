import asyncio
from config.settings import SETTINGS
from app.execution.quote import get_quote
from app.execution.jupiter import order
from app.alpha.alpha import alpha
from app.sources.pump import fetch_pump_candidates
from app.core.state import engine

SOL = "So11111111111111111111111111111111111111112"

LAST_TRADE = {}
COOLDOWN = SETTINGS["TOKEN_COOLDOWN"]

def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-500:]


# ================= SIZE =================
def get_size(score):
    if score >= SETTINGS["SNIPER_THRESHOLD"]:
        return SETTINGS["MAX_SIZE"]

    if score >= SETTINGS["FAST_ENTRY_THRESHOLD"]:
        return int(SETTINGS["BASE_SIZE"] * 1.8)

    return SETTINGS["BASE_SIZE"]


# ================= SELL =================
async def manage_positions():
    for pos in engine.positions[:]:
        try:
            m = pos["mint"]

            q = await get_quote(m, SOL, pos["size"])
            if not q:
                continue

            out_now = int(q.get("outAmount", 0) or 0)
            entry = pos["entry_out"]

            if out_now > pos["peak"]:
                pos["peak"] = out_now

            pnl = (out_now - entry) / max(entry, 1)

            log(f"PNL {m[:6]} {pnl:.4f}")

            # 💰 TAKE PROFIT
            if pnl > SETTINGS["TAKE_PROFIT"]:
                log(f"💰 TP {m[:6]}")
                engine.positions.remove(pos)
                engine.capital *= (1 + pnl)
                continue

            # 🔵 TRAILING STOP
            drawdown = (out_now - pos["peak"]) / max(pos["peak"], 1)
            if pos["peak"] > entry and drawdown < SETTINGS["TRAILING_STOP"]:
                log(f"🔵 TRAIL {m[:6]}")
                engine.positions.remove(pos)
                engine.capital *= (1 + pnl)
                continue

            # 🔴 STOP LOSS
            if pnl < SETTINGS["STOP_LOSS"]:
                log(f"🔴 SL {m[:6]}")
                engine.positions.remove(pos)
                engine.capital *= (1 + pnl)
                continue

        except Exception as e:
            log(f"SELL_ERR {e}")


# ================= BUY =================
async def try_trade(item):
    try:
        m = item.get("mint")
        if not m:
            return

        now = asyncio.get_event_loop().time()

        # ❗ 防重複持倉
        if any(p["mint"] == m for p in engine.positions):
            log(f"ALREADY_HELD {m[:6]}")
            return

        # ❗ 滿倉限制
        if len(engine.positions) >= SETTINGS["MAX_POSITIONS"]:
            log("MAX_POSITIONS")
            return

        # ❗ cooldown
        if now - LAST_TRADE.get(m, 0) < COOLDOWN:
            return

        score = await alpha(m)

        log(f"SCORE {m[:6]} {score:.4f}")

        if score < SETTINGS["ENTRY_THRESHOLD"]:
            return

        size = get_size(score)

        q = await get_quote(SOL, m, size)
        if not q:
            log(f"NO_QUOTE {m[:6]}")
            return

        out = int(q.get("outAmount", 0) or 0)
        impact = float(q.get("priceImpactPct", 0) or 0)

        if impact > SETTINGS["LIQUIDITY_IMPACT_MAX"]:
            log(f"HIGH_IMPACT {m[:6]}")
            return

        log(f"BUY {m[:6]} size={size} out={out} impact={impact:.4f}")

        o = await order(SOL, m, size, quote=q)
        if not o or not o.get("transaction"):
            log(f"ORDER_FAIL {m[:6]}")
            return

        engine.positions.append({
            "mint": m,
            "entry_out": out,
            "size": size,
            "peak": out,
            "time": now,
        })

        LAST_TRADE[m] = now
        engine.stats["executed"] += 1

        log(f"EXECUTED {m[:6]}")

    except Exception as e:
        log(f"TRADE_ERR {e}")


# ================= MAIN =================
async def main_loop():
    print("🚀 V6.1 ENGINE START")

    if not hasattr(engine, "positions"):
        engine.positions = []

    if not hasattr(engine, "capital"):
        engine.capital = 1.0

    while True:
        try:
            # 🧠 先處理賣出
            await manage_positions()

            # 🧠 再找新幣
            items = await fetch_pump_candidates()

            for item in items[:SETTINGS["TOP_N"]]:
                await try_trade(item)

        except Exception as e:
            log(f"LOOP_ERR {e}")

        await asyncio.sleep(3)

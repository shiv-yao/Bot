# ================= v1110_CLEAN_MONITOR =================

import os, asyncio, random, time
from collections import defaultdict
import httpx

from state import engine
from mempool import mempool_stream

# ================= CONFIG =================

RPC = os.getenv("RPC", "")
SOL = "So11111111111111111111111111111111111111112"
PUMP_API = "https://frontend-api.pump.fun/coins/latest"

MAX_POSITION_SOL = 0.0025
MIN_POSITION_SOL = 0.001

# ================= INIT =================

if not hasattr(engine, "positions"):
    engine.positions = []

engine.logs = []
engine.trade_history = []
engine.capital = getattr(engine, "capital", 1.0)
engine.loss_streak = getattr(engine, "loss_streak", 0)
engine.peak_capital = getattr(engine, "peak_capital", 1.0)

engine.last_trade = ""
engine.last_signal = ""

if not hasattr(engine, "stats"):
    engine.stats = {
        "signals": 0,
        "buys": 0,
        "sells": 0,
        "errors": 0,
    }

HTTP = httpx.AsyncClient(timeout=10)

# ================= STATE =================

CANDIDATES = set()
LAST_PRICE = {}
SMART_WALLETS = set()
WALLET_SCORE = defaultdict(float)
TOKEN_COOLDOWN = defaultdict(float)

# ================= UTILS =================

def log(msg: str):
    engine.logs.append(msg)
    engine.logs = engine.logs[-200:]
    print(msg)

def now():
    return time.time()

def clamp(v, lo, hi):
    return max(lo, min(v, hi))

# ================= SMART MONEY =================

async def fake_wallet_detector(mint):
    score = random.random()

    if score > 0.97:
        wallet = f"wallet_{mint[:4]}"
        SMART_WALLETS.add(wallet)
        WALLET_SCORE[wallet] += 1

        log(f"SMART_WALLET_DETECTED {wallet}")
        return True

    return False


def wallet_weight():
    if not WALLET_SCORE:
        return 1.0

    avg = sum(WALLET_SCORE.values()) / len(WALLET_SCORE)
    return clamp(1 + avg * 0.1, 1.0, 2.0)

# ================= PRICE =================

async def get_price(mint):
    try:
        r = await HTTP.get(
            "https://lite-api.jup.ag/swap/v1/quote",
            params={
                "inputMint": mint,
                "outputMint": SOL,
                "amount": "1000000"
            }
        )
        out = int(r.json().get("outAmount", 0)) / 1e9
        return out / 1_000_000 if out > 0 else None
    except Exception:
        engine.stats["errors"] += 1
        return None

# ================= ALPHA =================

async def momentum(mint):
    p1 = await get_price(mint)
    await asyncio.sleep(0.1)
    p2 = await get_price(mint)

    if not p1 or not p2:
        return 0.0

    return (p2 - p1) / p1


async def volume(mint):
    p = await get_price(mint)
    if not p:
        return 0.0

    prev = LAST_PRICE.get(mint, p)
    LAST_PRICE[mint] = p

    return abs(p - prev) / prev if prev > 0 else 0.0


async def alpha_fusion(mint):

    m = await momentum(mint)
    v = await volume(mint)
    smart = await fake_wallet_detector(mint)

    score = m * 0.5 + v * 0.3 + (0.2 if smart else 0.0)

    return score

# ================= EXEC =================

async def buy(mint, alpha):

    if now() - TOKEN_COOLDOWN[mint] < 15:
        return False

    if any(p["token"] == mint for p in engine.positions):
        return False

    price = await get_price(mint)
    if not price:
        return False

    size = 0.002 * (1 + alpha * 5) * wallet_weight()
    size = clamp(size, MIN_POSITION_SOL, MAX_POSITION_SOL)

    amount = size / price

    engine.positions.append({
        "token": mint,
        "amount": amount,
        "entry": price,
        "alpha": alpha,
        "peak": price
    })

    TOKEN_COOLDOWN[mint] = now()

    engine.stats["buys"] += 1
    engine.last_trade = f"BUY {mint[:6]} size={round(size,4)}"

    log(f"BUY {mint[:6]} alpha={round(alpha,5)} size={round(size,4)}")

    return True


async def sell(pos):

    mint = pos["token"]
    price = await get_price(mint)
    if not price:
        return False

    pnl = (price - pos["entry"]) * pos["amount"]

    engine.capital += pnl

    if pnl > 0:
        engine.loss_streak = 0
    else:
        engine.loss_streak += 1

    engine.trade_history.append({
        "mint": mint,
        "pnl": pnl
    })
    engine.trade_history = engine.trade_history[-200:]

    engine.positions.remove(pos)

    engine.stats["sells"] += 1
    engine.last_trade = f"SELL {mint[:6]} pnl={round(pnl,6)}"

    log(f"SELL {mint[:6]} pnl={round(pnl,6)}")

    return True

# ================= MONITOR =================

async def monitor():
    while True:
        try:
            for p in list(engine.positions):

                price = await get_price(p["token"])
                if not price:
                    continue

                p["peak"] = max(p["peak"], price)

                pnl_pct = (price - p["entry"]) / p["entry"]

                if pnl_pct > 0.18 or pnl_pct < -0.07:
                    await sell(p)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"MONITOR_ERROR {e}")

        await asyncio.sleep(2)

# ================= PUMP =================

async def pump_scanner():
    while True:
        try:
            r = await HTTP.get(PUMP_API)
            data = r.json()

            for c in data[:20]:
                mint = c.get("mint")
                if mint:
                    CANDIDATES.add(mint)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"PUMP_ERROR {e}")

        await asyncio.sleep(5)

# ================= MAIN =================

async def bot():

    log("ENGINE_START v1110")

    asyncio.create_task(monitor())
    asyncio.create_task(pump_scanner())

    try:
        asyncio.create_task(mempool_stream(lambda e: CANDIDATES.add(e.get("mint"))))
    except:
        log("MEMPOOL_DISABLED")

    while True:

        try:

            if not CANDIDATES:
                await asyncio.sleep(1)
                continue

            for mint in list(CANDIDATES):

                alpha = await alpha_fusion(mint)

                engine.stats["signals"] += 1
                engine.last_signal = f"{mint[:6]} alpha={round(alpha,5)}"

                log(f"SCAN {mint[:6]} alpha={round(alpha,5)}")

                if alpha < 0.01:
                    continue

                await buy(mint, alpha)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"LOOP_ERROR {e}")

        await asyncio.sleep(2)

# ================= ENTRY =================

async def bot_loop():
    await bot()

if __name__ == "__main__":
    asyncio.run(bot())

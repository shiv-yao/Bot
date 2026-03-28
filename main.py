import asyncio
import random
import httpx
import time

from contextlib import asynccontextmanager
from fastapi import FastAPI

# ================= STATE =================

STATE = {
    "positions": [],
    "closed_trades": [],
    "signals": 0,
    "errors": 0,
    "last_action": None,
    "candidates": [],
    "last_execution": None,
    "realized_pnl": 0.0,
}

MAX_POSITIONS = 3
MAX_DAILY_TRADES = 20
MAX_HOLD_SECONDS = 120
BASE_FAIL_RATE = 0.05
GAS_COST = 0.000005

STATE["daily_trades"] = 0
STATE["last_reset"] = time.time()

# ================= HELPERS =================

def has_position(mint: str) -> bool:
    return any(p.get("token") == mint for p in STATE["positions"])


async def get_quote(mint: str):
    try:
        sol = "So11111111111111111111111111111111111111112"
        amount_in = 1_000_000

        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": sol,
                    "outputMint": mint,
                    "amount": str(amount_in),
                    "slippageBps": 100
                }
            )

            if r.status_code != 200:
                return None

            data = r.json()
            out = int(data.get("outAmount", 0))

            if out == 0:
                return None

            price = amount_in / out
            impact = float(data.get("priceImpactPct", 0) or 0)

            return {
                "price": price,
                "impact": impact
            }

    except:
        return None


async def real_alpha(mint: str) -> float:
    try:
        q1 = await get_quote(mint)
        await asyncio.sleep(0.2)
        q2 = await get_quote(mint)

        if not q1 or not q2:
            return 0

        p1 = q1["price"]
        p2 = q2["price"]

        strength = (p2 - p1) / p1
        liquidity_score = min(1 / p1, 3) * 25
        impact_penalty = q1["impact"] * 100

        alpha = strength * 4000 + liquidity_score - impact_penalty
        return round(alpha, 2)

    except:
        return 0


async def scan_tokens():
    tokens = []

    # ===== Pump.fun =====
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get("https://frontend-api.pump.fun/coins")

            if r.status_code == 200:
                data = r.json()

                for item in data[:20]:
                    mint = item.get("mint")

                    # 🚨 過濾垃圾 token
                    if mint and len(mint) > 30:
                        tokens.append(mint)

    except Exception as e:
        STATE["scanner_error"] = str(e)

    # ===== 如果 pump 壞了 → fallback dex =====
    if not tokens:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get("https://api.dexscreener.com/latest/dex/pairs/solana")

                if r.status_code == 200:
                    data = r.json().get("pairs", [])

                    for p in data[:20]:
                        mint = p.get("baseToken", {}).get("address")
                        if mint:
                            tokens.append(mint)

            STATE["scanner_mode"] = "dexscreener"

        except Exception as e:
            STATE["scanner_error"] = str(e)

    else:
        STATE["scanner_mode"] = "pump"

    # 🚨 最後 fallback
    if not tokens:
        tokens = ["TEST_A", "TEST_B"]
        STATE["scanner_mode"] = "fallback"

    return tokens

# ================= EXECUTION =================

async def simulate_buy(mint: str, size: float):
    quote = await get_quote(mint)
    if not quote:
        return None

    price = quote["price"]
    impact = quote["impact"]

    if impact > 0.2:
        STATE["last_action"] = f"skip_illiquid:{mint}"
        return None

    base_slippage = random.uniform(0.002, 0.02)
    slippage = max(base_slippage, impact * random.uniform(1.2, 2.0))
    fill_price = price * (1 + slippage)

    # 動態失敗率
    fail_rate = BASE_FAIL_RATE + impact * 2
    if random.random() < fail_rate:
        STATE["last_action"] = f"fill_fail:{mint}:{fail_rate:.2f}"
        return None

    token_qty = size / fill_price if fill_price > 0 else 0.0

    result = {
        "ok": True,
        "mint": mint,
        "size": size,
        "mark_price": price,
        "fill_price": fill_price,
        "token_qty": token_qty,
        "gas_cost": GAS_COST,
        "impact": impact,
        "slippage": slippage,
        "side": "buy",
    }

    STATE["last_execution"] = result
    return result


async def simulate_sell(pos: dict, reason: str):
    quote = await get_quote(pos["token"])
    if not quote:
        return None

    price = quote["price"]
    impact = quote["impact"]

    base_slippage = random.uniform(0.002, 0.015)
    slippage = max(base_slippage, impact * random.uniform(1.0, 1.8))
    fill_price = price * (1 - slippage)

    entry_price = pos["entry_price"]
    pnl_pct = (fill_price - entry_price) / entry_price

    gross_pnl = pos["size"] * pnl_pct
    net_pnl = gross_pnl - GAS_COST

    result = {
        "ok": True,
        "mint": pos["token"],
        "mark_price": price,
        "fill_price": fill_price,
        "entry_price": entry_price,
        "pnl_pct": pnl_pct,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "gas_cost": GAS_COST,
        "impact": impact,
        "slippage": slippage,
        "reason": reason,
        "side": "sell",
    }

    STATE["last_execution"] = result
    return result

# ================= MONITOR =================

async def monitor_positions():
    for pos in STATE["positions"][:]:
        quote = await get_quote(pos["token"])
        if not quote:
            continue

        price = quote["price"]
        pos["last_price"] = price

        entry = pos["entry_price"]
        pnl_pct = (price - entry) / entry
        pos["pnl_pct"] = pnl_pct

        hold_time = time.time() - pos["entry_time"]

        reason = None

        if pnl_pct > 0.1:
            reason = "take_profit"
        elif pnl_pct < -0.05:
            reason = "stop_loss"
        elif hold_time > MAX_HOLD_SECONDS:
            reason = "timeout_exit"

        if reason:
            result = await simulate_sell(pos, reason)
            if result:
                STATE["closed_trades"].append(result)
                STATE["positions"].remove(pos)
                STATE["realized_pnl"] += result["net_pnl"]

# ================= BOT LOOP =================

async def bot_loop():
    while True:
        try:
            # reset daily
            now = time.time()
            if now - STATE["last_reset"] > 86400:
                STATE["daily_trades"] = 0
                STATE["last_reset"] = now

            STATE["signals"] += 1
            STATE["last_action"] = "scan"

            tokens = await scan_tokens()
            STATE["candidates"] = tokens

            await monitor_positions()

            for mint in tokens:

                if STATE["daily_trades"] >= MAX_DAILY_TRADES:
                    STATE["last_action"] = "daily_limit_hit"
                    break

                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                if has_position(mint):
                    continue

                alpha = await real_alpha(mint)
                STATE["last_alpha"] = {"mint": mint, "alpha": alpha}

                if alpha < 120:
                    STATE["last_action"] = f"alpha_skip:{mint}:{alpha}"
                    continue

                if mint.startswith("TEST_"):
                    continue

                result = await simulate_buy(mint, 0.01)
                if not result:
                    continue

                STATE["positions"].append({
                    "token": mint,
                    "alpha": alpha,
                    "size": result["size"],
                    "entry_price": result["fill_price"],
                    "token_qty": result["token_qty"],
                    "entry_time": time.time(),
                    "entry_gas_cost": GAS_COST
                })

                STATE["daily_trades"] += 1
                STATE["last_action"] = f"buy:{mint}"

        except Exception as e:
            STATE["errors"] += 1
            STATE["last_action"] = f"error:{str(e)}"

        await asyncio.sleep(2)

# ================= FASTAPI =================

bot_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task
    bot_task = asyncio.create_task(bot_loop())
    yield
    if bot_task:
        bot_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"ok": True, "status": "running"}

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/metrics")
async def metrics():
    return STATE

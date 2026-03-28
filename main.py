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

async def has_jupiter_route(mint: str) -> bool:
    try:
        sol = "So11111111111111111111111111111111111111112"

        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": sol,
                    "outputMint": mint,
                    "amount": "1000000",
                    "slippageBps": 100,
                },
            )

            if r.status_code != 200:
                return False

            data = r.json()
            out = int(data.get("outAmount", 0) or 0)

            return out > 1000

    except Exception:
        return False

async def real_alpha(mint: str) -> float:
    try:
        sol = "So11111111111111111111111111111111111111112"

        async with httpx.AsyncClient(timeout=8) as client:

            # STEP 1: SOL → TOKEN
            r1 = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": sol,
                    "outputMint": mint,
                    "amount": 1_000_000,
                    "slippageBps": 100
                }
            )

            if r1.status_code != 200:
                return -999

            q1 = r1.json()
            out_token = int(q1.get("outAmount", 0) or 0)

            if out_token <= 0:
                return -999

            # STEP 2: TOKEN → SOL（回來）
            r2 = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": mint,
                    "outputMint": sol,
                    "amount": out_token,
                    "slippageBps": 100
                }
            )

            if r2.status_code != 200:
                return -999

            q2 = r2.json()
            back_sol = int(q2.get("outAmount", 0) or 0)

            if back_sol <= 0:
                return -999

            # =========================
            # 核心：round-trip pnl
            # =========================
            pnl = (back_sol - 1_000_000) / 1_000_000

            impact = float(q1.get("priceImpactPct", 0.0))
            liquidity_score = min(out_token / 100000, 3) * 25

            alpha = pnl * 3000 + liquidity_score - impact * 100

            return round(alpha, 2)

    except:
        return -999
async def scan_tokens():
    tokens = []

    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get(
                "https://api.dexscreener.com/latest/dex/search",
                params={"q": "SOL"},
            )

            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])[:30]

                for p in pairs:
                    chain = p.get("chainId")
                    mint = p.get("baseToken", {}).get("address")

                    # ✅ 只留 Solana
                    if chain != "solana":
                        continue

                    # ✅ 過濾奇怪地址（非 mint）
                    if not mint or len(mint) < 32:
                        continue

                    tokens.append(mint)

                STATE["scanner_mode"] = "dexscreener_filtered"
                STATE["scanner_error"] = None

    except Exception as e:
        STATE["scanner_mode"] = "fallback"
        STATE["scanner_error"] = str(e)

    if not tokens:
        tokens = ["TEST_A", "TEST_B"]

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
            STATE["bot_version"] = "alpha_dual_engine_v2"

            now = time.time()
            if now - STATE["last_reset"] > 86400:
                STATE["daily_trades"] = 0
                STATE["last_reset"] = now

            STATE["signals"] += 1
            STATE["last_action"] = "scan"

            raw_tokens = await scan_tokens()

            stable_tokens = []
            degen_tokens = []

            # =========================
            # 分流：有 Jupiter route = stable
            # 沒 route / 太早期 = degen
            # =========================
            async with httpx.AsyncClient(timeout=4) as client:
                for mint in raw_tokens:
                    try:
                        r = await client.get(
                            "https://lite-api.jup.ag/swap/v1/quote",
                            params={
                                "inputMint": "So11111111111111111111111111111111111111112",
                                "outputMint": mint,
                                "amount": 1000000,
                                "slippageBps": 100,
                            },
                        )

                        if r.status_code == 200:
                            q = r.json()
                            if int(q.get("outAmount", 0) or 0) > 0:
                                stable_tokens.append(mint)
                            else:
                                degen_tokens.append(mint)
                        else:
                            degen_tokens.append(mint)

                    except Exception:
                        degen_tokens.append(mint)

            STATE["candidates"] = stable_tokens + degen_tokens

            await monitor_positions()

            # =========================
            # 🟢 ENGINE 1：穩定賺（只打有 route）
            # =========================
            for mint in stable_tokens:
                if STATE["daily_trades"] >= MAX_DAILY_TRADES:
                    STATE["last_action"] = "daily_limit_hit"
                    break

                if len(STATE["positions"]) >= MAX_POSITIONS:
                    STATE["last_action"] = "position_limit"
                    break

                if has_position(mint):
                    STATE["last_action"] = f"already_have:{mint}"
                    continue

                if not mint or len(mint) < 32:
                    STATE["last_action"] = f"bad_mint:{mint}"
                    continue

                if any(c in mint for c in [".", "/", ":"]):
                    STATE["last_action"] = f"weird_mint:{mint}"
                    continue

                alpha = await real_alpha(mint)
                STATE["last_alpha"] = {"mint": mint, "alpha": alpha}

                # 穩定引擎：垃圾幣直接跳過
                if alpha == -999:
                    STATE["last_action"] = f"stable_skip_bad:{mint}"
                    continue

                if alpha < 15:
                    STATE["last_action"] = f"stable_alpha_skip:{mint}:{alpha}"
                    continue

                exec_result = await simulate_buy(mint, 0.01)
                if not exec_result:
                    continue

                if exec_result["slippage"] > 0.01:
                    STATE["last_action"] = f"slippage_skip:{mint}:{exec_result['slippage']:.4f}"
                    continue

                STATE["positions"].append({
                    "token": mint,
                    "alpha": alpha,
                    "size": exec_result["size"],
                    "entry_price": exec_result["fill_price"],
                    "mark_price": exec_result["mark_price"],
                    "last_price": exec_result["fill_price"],
                    "token_qty": exec_result["token_qty"],
                    "entry_time": time.time(),
                    "entry_gas_cost": exec_result["gas_cost"],
                    "pnl_pct": 0.0,
                    "engine": "stable",
                })

                STATE["daily_trades"] += 1
                STATE["last_action"] = f"stable_buy:{mint}"

            # =========================
            # 🔴 ENGINE 2：打仗（早期幣）
            # =========================
            for mint in degen_tokens[:3]:
                if STATE["daily_trades"] >= MAX_DAILY_TRADES:
                    STATE["last_action"] = "daily_limit_hit"
                    break

                if len(STATE["positions"]) >= MAX_POSITIONS:
                    STATE["last_action"] = "position_limit"
                    break

                if has_position(mint):
                    STATE["last_action"] = f"already_have:{mint}"
                    continue

                if not mint or len(mint) < 32:
                    STATE["last_action"] = f"bad_mint:{mint}"
                    continue

                if any(c in mint for c in [".", "/", ":"]):
                    STATE["last_action"] = f"weird_mint:{mint}"
                    continue

                alpha = await real_alpha(mint)
                STATE["last_alpha"] = {"mint": mint, "alpha": alpha}

                # 🔥 關鍵：early coin fallback
                if alpha == -999:
                    alpha = round(random.uniform(20, 60), 2)
                    STATE["last_alpha"] = {"mint": mint, "alpha": alpha}
                    STATE["last_action"] = f"degen_fallback_alpha:{mint}:{alpha}"

                if alpha < 20:
                    STATE["last_action"] = f"degen_alpha_skip:{mint}:{alpha}"
                    continue

                exec_result = await simulate_buy(mint, 0.005)
                if not exec_result:
                    continue

                STATE["positions"].append({
                    "token": mint,
                    "alpha": alpha,
                    "size": exec_result["size"],
                    "entry_price": exec_result["fill_price"],
                    "mark_price": exec_result["mark_price"],
                    "last_price": exec_result["fill_price"],
                    "token_qty": exec_result["token_qty"],
                    "entry_time": time.time(),
                    "entry_gas_cost": exec_result["gas_cost"],
                    "pnl_pct": 0.0,
                    "engine": "degen",
                })

                STATE["daily_trades"] += 1
                STATE["last_action"] = f"degen_buy:{mint}"

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

# 👉 就放在這裡 👇
@app.get("/metrics")
async def metrics():
    return {
        "positions": STATE["positions"],
        "closed_trades": STATE["closed_trades"],
        "signals": STATE["signals"],
        "errors": STATE["errors"],
        "last_action": STATE["last_action"],
        "candidates": STATE["candidates"],
        "last_execution": STATE["last_execution"],
        "realized_pnl": STATE["realized_pnl"],
        "daily_trades": STATE["daily_trades"],
        "last_reset": STATE["last_reset"],
        "scanner_mode": STATE.get("scanner_mode"),
        "scanner_error": STATE.get("scanner_error"),
        "last_alpha": STATE.get("last_alpha"),
        "bot_version": STATE.get("bot_version"),
        "candidate_count": STATE.get("candidate_count"),
    }

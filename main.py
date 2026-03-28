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
    "daily_pnl": 0.0,
    "daily_trades": 0,
    "last_reset": time.time(),
    "scanner_mode": None,
    "scanner_error": None,
    "last_alpha": None,
    "bot_version": "alpha_dual_engine_v3_risk_tuned",
    "candidate_count": 0,
}

# ================= CONFIG =================

MAX_POSITIONS = 4
MAX_DAILY_TRADES = 20
MAX_HOLD_SECONDS = 120
BASE_FAIL_RATE = 0.05
GAS_COST = 0.000005

BASE_SIZE = 0.003
STOP_LOSS = -0.10
TAKE_PROFIT = 0.20
DAILY_STOP = -0.03

# ================= HELPERS =================

def has_position(mint: str) -> bool:
    return any(p.get("token") == mint for p in STATE["positions"])


def is_valid_solana_mint(mint: str) -> bool:
    if not mint:
        return False
    if len(mint) < 32 or len(mint) > 44:
        return False
    if any(c in mint for c in [".", "/", ":"]):
        return False
    if mint.startswith("0x"):
        return False
    return True


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
                    "slippageBps": 100,
                },
            )

            if r.status_code != 200:
                return None

            data = r.json()
            out = int(data.get("outAmount", 0) or 0)

            if out <= 0:
                return None

            price = amount_in / out
            impact = float(data.get("priceImpactPct", 0) or 0)

            return {
                "price": price,
                "impact": impact,
                "out": out,
            }

    except Exception:
        return None


async def has_jupiter_route(mint: str) -> bool:
    q = await get_quote(mint)
    return q is not None and q["out"] > 1000


async def real_alpha(mint: str) -> float:
    try:
        sol = "So11111111111111111111111111111111111111112"

        async with httpx.AsyncClient(timeout=8) as client:
            # STEP 1: SOL -> TOKEN
            r1 = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": sol,
                    "outputMint": mint,
                    "amount": 1_000_000,
                    "slippageBps": 100,
                },
            )

            if r1.status_code != 200:
                return -999.0

            q1 = r1.json()
            out_token = int(q1.get("outAmount", 0) or 0)

            if out_token <= 0:
                return -999.0

            # STEP 2: TOKEN -> SOL
            r2 = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": mint,
                    "outputMint": sol,
                    "amount": out_token,
                    "slippageBps": 100,
                },
            )

            if r2.status_code != 200:
                return -999.0

            q2 = r2.json()
            back_sol = int(q2.get("outAmount", 0) or 0)

            if back_sol <= 0:
                return -999.0

            pnl = (back_sol - 1_000_000) / 1_000_000
            impact = float(q1.get("priceImpactPct", 0.0) or 0.0)
            liquidity_score = min(out_token / 100000, 3) * 25

            alpha = pnl * 3000 + liquidity_score - impact * 100
            return round(alpha, 2)

    except Exception:
        return -999.0


async def scan_tokens():
    tokens = []
    STATE["scanner_error"] = None

    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get(
                "https://api.dexscreener.com/latest/dex/search",
                params={"q": "SOL"},
            )

            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])[:30]

                seen = set()

                for p in pairs:
                    chain = p.get("chainId")
                    mint = p.get("baseToken", {}).get("address")

                    if chain != "solana":
                        continue
                    if not is_valid_solana_mint(mint):
                        continue
                    if mint in seen:
                        continue

                    seen.add(mint)
                    tokens.append(mint)

                STATE["scanner_mode"] = "dexscreener_filtered"
                STATE["scanner_error"] = None
            else:
                STATE["scanner_mode"] = "fallback"
                STATE["scanner_error"] = f"dex_status_{r.status_code}"

    except Exception as e:
        STATE["scanner_mode"] = "fallback"
        STATE["scanner_error"] = str(e)

    if not tokens:
        tokens = ["TEST_A", "TEST_B"]
        STATE["scanner_mode"] = "fallback"

    STATE["candidate_count"] = len(tokens)
    return tokens


# ================= EXECUTION =================

async def simulate_buy(mint: str, size: float):
    try:
        # 模擬成交，保證系統能測試完整流程
        price = random.uniform(0.00001, 0.00002)
        token_qty = size / price

        result = {
            "ok": True,
            "mint": mint,
            "size": size,
            "mark_price": price,
            "fill_price": price,
            "token_qty": token_qty,
            "gas_cost": GAS_COST,
            "slippage": random.uniform(0, 0.01),
            "timestamp": time.time(),
        }

        STATE["last_execution"] = result
        return result

    except Exception:
        return None


async def simulate_sell(position: dict):
    try:
        price = position["entry_price"] * random.uniform(0.7, 1.3)
        token_qty = position["token_qty"]

        value = token_qty * price
        entry_value = token_qty * position["entry_price"]

        pnl = value - entry_value
        pnl_pct = pnl / entry_value

        result = {
            "ok": True,
            "price": price,
            "value": value,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "gas_cost": GAS_COST,
            "timestamp": time.time(),
            "token": position["token"],
            "engine": position.get("engine"),
        }

        STATE["last_execution"] = result
        return result

    except Exception:
        return None


# ================= MONITOR =================

async def monitor_positions():
    new_positions = []

    for pos in STATE["positions"]:
        result = await simulate_sell(pos)
        if not result:
            new_positions.append(pos)
            continue

        pos["mark_price"] = result["price"]
        pos["last_price"] = result["price"]
        pos["pnl_pct"] = result["pnl_pct"]

        if pos["pnl_pct"] < STOP_LOSS:
            closed = dict(pos)
            closed["exit_price"] = result["price"]
            closed["exit_time"] = result["timestamp"]
            closed["exit_reason"] = "stop_loss"
            closed["realized_pnl"] = result["pnl"] - result["gas_cost"]

            STATE["closed_trades"].append(closed)
            STATE["realized_pnl"] += closed["realized_pnl"]
            STATE["daily_pnl"] += closed["realized_pnl"]
            STATE["last_action"] = f"stop_loss:{pos['token']}"
            continue

        if pos["pnl_pct"] > TAKE_PROFIT:
            closed = dict(pos)
            closed["exit_price"] = result["price"]
            closed["exit_time"] = result["timestamp"]
            closed["exit_reason"] = "take_profit"
            closed["realized_pnl"] = result["pnl"] - result["gas_cost"]

            STATE["closed_trades"].append(closed)
            STATE["realized_pnl"] += closed["realized_pnl"]
            STATE["daily_pnl"] += closed["realized_pnl"]
            STATE["last_action"] = f"take_profit:{pos['token']}"
            continue

        if time.time() - pos["entry_time"] > MAX_HOLD_SECONDS:
            closed = dict(pos)
            closed["exit_price"] = result["price"]
            closed["exit_time"] = result["timestamp"]
            closed["exit_reason"] = "timeout"
            closed["realized_pnl"] = result["pnl"] - result["gas_cost"]

            STATE["closed_trades"].append(closed)
            STATE["realized_pnl"] += closed["realized_pnl"]
            STATE["daily_pnl"] += closed["realized_pnl"]
            STATE["last_action"] = f"timeout_exit:{pos['token']}"
            continue

        new_positions.append(pos)

    STATE["positions"] = new_positions


# ================= BOT LOOP =================

async def bot_loop():
    while True:
        try:
            STATE["bot_version"] = "alpha_dual_engine_v3_risk_tuned"

            now = time.time()
            if now - STATE["last_reset"] > 86400:
                STATE["daily_trades"] = 0
                STATE["daily_pnl"] = 0.0
                STATE["last_reset"] = now

            if STATE["daily_pnl"] < DAILY_STOP:
                STATE["last_action"] = "daily_stop"
                await asyncio.sleep(10)
                continue

            STATE["signals"] += 1
            STATE["last_action"] = "scan"

            raw_tokens = await scan_tokens()

            stable_tokens = []
            degen_tokens = []

            async with httpx.AsyncClient(timeout=4) as client:
                for mint in raw_tokens:
                    try:
                        if not is_valid_solana_mint(mint):
                            continue

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
            STATE["candidate_count"] = len(STATE["candidates"])

            await monitor_positions()

            # =========================
            # ENGINE 1: STABLE
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

                alpha = await real_alpha(mint)
                STATE["last_alpha"] = {"mint": mint, "alpha": alpha}

                if alpha == -999:
                    STATE["last_action"] = f"stable_skip_bad:{mint}"
                    continue

                if alpha < 15:
                    STATE["last_action"] = f"stable_alpha_skip:{mint}:{alpha}"
                    continue

                exec_result = await simulate_buy(mint, 0.006)
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
            # ENGINE 2: DEGEN
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

                if not is_valid_solana_mint(mint):
                    STATE["last_action"] = f"bad_mint:{mint}"
                    continue

                alpha = await real_alpha(mint)
                STATE["last_alpha"] = {"mint": mint, "alpha": alpha}

                if alpha == -999:
                    alpha = round(random.uniform(20, 60), 2)
                    STATE["last_alpha"] = {"mint": mint, "alpha": alpha}
                    STATE["last_action"] = f"degen_fallback_alpha:{mint}:{alpha}"

                if alpha < 20:
                    STATE["last_action"] = f"degen_alpha_skip:{mint}:{alpha}"
                    continue

                exec_result = await simulate_buy(mint, 0.002)
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
        "daily_pnl": STATE["daily_pnl"],
        "daily_trades": STATE["daily_trades"],
        "last_reset": STATE["last_reset"],
        "scanner_mode": STATE.get("scanner_mode"),
        "scanner_error": STATE.get("scanner_error"),
        "last_alpha": STATE.get("last_alpha"),
        "bot_version": STATE.get("bot_version"),
        "candidate_count": STATE.get("candidate_count"),
    }

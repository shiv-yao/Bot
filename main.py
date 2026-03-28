import asyncio
import random
import httpx

from contextlib import asynccontextmanager
from fastapi import FastAPI

STATE = {
    "positions": [],
    "closed_trades": [],
    "signals": 0,
    "errors": 0,
    "last_action": None,
    "candidates": [],
    "scanner_mode": None,
    "scanner_error": None,
    "dex_pairs": 0,
    "realized_pnl": 0.0,
    "last_alpha": None,
}

MAX_POSITIONS = 3
TAKE_PROFIT = 0.08
STOP_LOSS = 0.05
DAILY_LOSS_LIMIT = -0.02


def has_position(mint: str) -> bool:
    return any(p.get("token") == mint for p in STATE["positions"])


def is_solana_mint(addr: str) -> bool:
    if not isinstance(addr, str):
        return False
    if addr.startswith("0x"):
        return False
    return 32 <= len(addr) <= 44


async def real_alpha(mint: str) -> float:
    try:
        sol = "So11111111111111111111111111111111111111112"

        async with httpx.AsyncClient(timeout=8) as client:
            r1 = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": sol,
                    "outputMint": mint,
                    "amount": "1000000",
                    "slippageBps": 100,
                },
            )

            if r1.status_code != 200:
                return 0.0

            q1 = r1.json()
            out1 = int(q1.get("outAmount", 0) or 0)
            impact = float(q1.get("priceImpactPct", 1) or 1)

            await asyncio.sleep(0.2)

            r2 = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": sol,
                    "outputMint": mint,
                    "amount": "1000000",
                    "slippageBps": 100,
                },
            )

            if r2.status_code != 200:
                return 0.0

            q2 = r2.json()
            out2 = int(q2.get("outAmount", 0) or 0)

            if out1 <= 0:
                return 0.0

            strength = (out2 - out1) / out1
            liquidity_score = min(out1 / 100000, 3) * 25
            impact_penalty = impact * 100

            alpha = strength * 4000 + liquidity_score - impact_penalty
            return round(alpha, 2)

    except Exception:
        return 0.0


async def get_real_price(mint: str):
    try:
        sol = "So11111111111111111111111111111111111111112"
        amount_in = 10_000_000  # 0.001 SOL

        async with httpx.AsyncClient(timeout=8) as client:
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

            # 🔥 正確價格（SOL per token）
            price = amount_in / out

            return price

    except Exception:
        return None


async def scan_tokens():
    tokens = []
    STATE["scanner_error"] = None
    STATE["dex_pairs"] = 0

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://api.dexscreener.com/latest/dex/search",
                params={"q": "solana"},
            )

            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", []) or []
                STATE["dex_pairs"] = len(pairs)

                seen = set()

                for p in pairs:
                    if p.get("chainId") != "solana":
                        continue

                    mint = p.get("baseToken", {}).get("address")
                    liquidity = p.get("liquidity", {}).get("usd", 0) or 0

                    try:
                        liquidity = float(liquidity)
                    except Exception:
                        liquidity = 0.0

                    if not is_solana_mint(mint):
                        continue

                    if liquidity < 20000:
                        continue

                    if mint in seen:
                        continue

                    seen.add(mint)
                    tokens.append(mint)

                STATE["scanner_mode"] = "dexscreener"
            else:
                STATE["scanner_error"] = f"dex_status_{r.status_code}"

    except Exception as e:
        STATE["scanner_error"] = str(e)

    if not tokens:
        tokens = [
            "So11111111111111111111111111111111111111112",
            "Es9vMFrzaCERmJfrF4H2Fy7pRkNvztNFVQVw1Gc7emsK",
        ]
        STATE["scanner_mode"] = "fallback"

    return tokens[:20]


async def monitor_positions():
    while True:
        try:
            still_open = []

            for pos in STATE["positions"]:
                price = await get_real_price(pos["token"])

                if price is None:
                    still_open.append(pos)
                    continue

                # 模擬市場波動，避免 quote 幾乎固定不動
                noise = random.uniform(-0.02, 0.03)
                current_price = price * (1 + noise)

                pnl_pct = (current_price - pos["entry_price"]) / pos["entry_price"]

                pos["last_price"] = round(current_price, 12)
                pos["pnl_pct"] = round(pnl_pct, 4)

                if pnl_pct >= TAKE_PROFIT:
                    pnl_value = pos["size"] * pnl_pct
                    STATE["realized_pnl"] += pnl_value
                    STATE["closed_trades"].append({
                        "token": pos["token"],
                        "entry_price": pos["entry_price"],
                        "exit_price": round(current_price, 12),
                        "pnl_pct": round(pnl_pct, 4),
                        "reason": "take_profit",
                    })
                    STATE["last_action"] = f"tp_sell:{pos['token']}"
                    continue

                if pnl_pct <= -STOP_LOSS:
                    pnl_value = pos["size"] * pnl_pct
                    STATE["realized_pnl"] += pnl_value
                    STATE["closed_trades"].append({
                        "token": pos["token"],
                        "entry_price": pos["entry_price"],
                        "exit_price": round(current_price, 12),
                        "pnl_pct": round(pnl_pct, 4),
                        "reason": "stop_loss",
                    })
                    STATE["last_action"] = f"sl_sell:{pos['token']}"
                    continue

                still_open.append(pos)

            STATE["positions"] = still_open

        except Exception as e:
            STATE["errors"] += 1
            STATE["last_action"] = f"monitor_error:{str(e)}"

        await asyncio.sleep(1)


async def bot_loop():
    while True:
        try:
            STATE["signals"] += 1
            STATE["last_action"] = "scan"

            if STATE["realized_pnl"] < DAILY_LOSS_LIMIT:
                STATE["last_action"] = "kill_switch"
                await asyncio.sleep(5)
                continue

            tokens = await scan_tokens()
            STATE["candidates"] = tokens

            for mint in tokens:
                if len(STATE["positions"]) >= MAX_POSITIONS:
                    STATE["last_action"] = "position_limit"
                    break

                if has_position(mint):
                    STATE["last_action"] = f"already_have:{mint}"
                    continue

                alpha = await real_alpha(mint)

                STATE["last_alpha"] = {
                    "mint": mint,
                    "alpha": alpha,
                }

                if alpha <= 0:
                    STATE["last_action"] = f"quote_fail:{mint}"
                    continue

                if alpha < 20:
                    STATE["last_action"] = f"alpha_skip:{mint}:{alpha}"
                    continue

                size = min(0.01, 0.1 / (len(STATE["positions"]) + 1))

                entry_price = await get_real_price(mint)
                if entry_price is None:
                    STATE["last_action"] = f"entry_price_fail:{mint}"
                    continue

                STATE["positions"].append({
                    "token": mint,
                    "alpha": round(alpha, 2),
                    "size": round(size, 4),
                    "entry_price": entry_price,
                    "last_price": entry_price,
                    "pnl_pct": 0.0,
                })

                STATE["last_action"] = f"paper_buy:{mint}"

        except Exception as e:
            STATE["errors"] += 1
            STATE["last_action"] = f"error:{str(e)}"

        await asyncio.sleep(2)


bot_task = None
monitor_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task, monitor_task
    bot_task = asyncio.create_task(bot_loop())
    monitor_task = asyncio.create_task(monitor_positions())
    yield
    if bot_task:
        bot_task.cancel()
    if monitor_task:
        monitor_task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return {"ok": True, "status": "running"}


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/data")
async def data():
    return STATE


@app.get("/metrics")
async def metrics():
    return {
        "positions": STATE["positions"],
        "closed_trades": STATE["closed_trades"][-10:],
        "signals": STATE["signals"],
        "errors": STATE["errors"],
        "last_action": STATE["last_action"],
        "last_alpha": STATE["last_alpha"],
        "candidates": STATE["candidates"],
        "scanner_mode": STATE.get("scanner_mode"),
        "scanner_error": STATE.get("scanner_error"),
        "dex_pairs": STATE.get("dex_pairs"),
        "realized_pnl": round(STATE["realized_pnl"], 6),
    }

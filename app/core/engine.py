import asyncio

from app.execution.quote import get_quote
from app.execution.jupiter import order
from app.alpha.alpha import alpha
from app.regime.regime import regime
from app.ai.tuner import tuner
from app.core.state import engine
from app.sources.pump import fetch_pump_candidates
from config.settings import SETTINGS

SOL = "So11111111111111111111111111111111111111112"

LAST_TRADE = {}
COOLDOWN = 60
MAX_TRADES = 20


def log(msg):
    msg = str(msg)
    print(msg)
    engine.logs.append(msg)
    engine.logs = engine.logs[-500:]


async def process(item):
    try:
        m = item.get("mint")
        if not m:
            return

        now = asyncio.get_event_loop().time()

        if engine.stats.get("executed", 0) >= MAX_TRADES:
            log("MAX_TRADES_REACHED")
            return

        last = LAST_TRADE.get(m, 0)
        if now - last < COOLDOWN:
            log(f"COOLDOWN {m[:6]}")
            return

        log(f"PROCESSING: {item}")
        engine.stats["signals"] += 1

        score = await alpha(m)

        regime.update(score)
        engine.regime = regime.mode
        score *= regime.multiplier()

        log(f"SCORE {m[:6]} score={score:.4f} thr={tuner.threshold:.4f}")

        if score < 0.012:
            engine.stats["rejected"] += 1
            log(f"REJECT {m[:6]} score={score:.4f} thr=0.012")
            return

        lamports = 200000  # 0.0002 SOL

        q = await get_quote(SOL, m, lamports)
        if not q:
            engine.stats["rejected"] += 1
            log(f"NO_QUOTE {m[:8]}")
            return

        out_amount = int(q.get("outAmount", 0) or 0)
        impact = float(q.get("priceImpactPct", 0) or 0)

        if out_amount <= 0:
            engine.stats["rejected"] += 1
            log(f"NO_LIQ_ROUTE {m[:8]}")
            return

        if out_amount < 50:
            engine.stats["rejected"] += 1
            log(f"TOO_SMALL {m[:6]} out={out_amount}")
            return

        if impact > SETTINGS["LIQUIDITY_IMPACT_MAX"]:
            engine.stats["rejected"] += 1
            log(f"HIGH_IMPACT {m[:8]} impact={impact:.4f}")
            return

        log(f"QUOTE_OK {m[:8]} out={out_amount} impact={impact:.4f}")

        o = await order(SOL, m, lamports, quote=q)
        if not o or not o.get("transaction"):
            engine.stats["rejected"] += 1
            log(f"ORDER_FAIL {m[:8]} data={o}")
            return

        log(
            f"PAPER_EXEC {m[:8]} "
            f"score={score:.4f} "
            f"regime={engine.regime} "
            f"impact={impact:.4f} "
            f"out={out_amount}"
        )

        LAST_TRADE[m] = now

        pnl = score - 0.01
        tuner.update(pnl)
        engine.threshold = tuner.threshold
        engine.capital *= (1 + pnl)

        engine.trade_history.append({
            "token": m,
            "score": score,
            "pnl": pnl,
            "regime": engine.regime,
            "impact": impact,
            "outAmount": out_amount,
            "mode": "paper_v2_stable",
        })

        engine.stats["executed"] += 1

    except Exception as e:
        engine.stats["errors"] += 1
        log(f"PROCESS_ERR {e}")


async def safe_cycle():
    print("scanning...")

    try:
        items = await fetch_pump_candidates()
        print("PUMP FILTERED:", items)

        for item in items:
            await process(item)

    except Exception as e:
        log(f"SAFE_CYCLE_ERR {e}")

    await asyncio.sleep(5)


async def main_loop():
    print("ENGINE LOOP START")

    if tuner.threshold < SETTINGS["TUNER_MIN"]:
        tuner.threshold = SETTINGS["TUNER_MIN"]
    if tuner.threshold > SETTINGS["TUNER_MAX"]:
        tuner.threshold = SETTINGS["TUNER_MAX"]

    while True:
        try:
            await safe_cycle()
        except Exception as e:
            engine.stats["errors"] += 1
            log(f"LOOP ERROR: {e}")
            await asyncio.sleep(2)

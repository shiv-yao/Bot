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
COOLDOWN = SETTINGS["TOKEN_COOLDOWN"]
MAX_TRADES = SETTINGS["MAX_TRADES"]
TOP_N = SETTINGS["TOP_N"]


def log(msg):
    msg = str(msg)
    print(msg)
    engine.logs.append(msg)
    engine.logs = engine.logs[-500:]


def dynamic_threshold(base_score: float) -> float:
    # 市場越強，門檻越低一點；市場越弱，門檻越高一點
    if engine.regime == "bull":
        return max(SETTINGS["TUNER_MIN"], tuner.threshold * 0.85)
    if engine.regime == "bear":
        return min(SETTINGS["TUNER_MAX"], tuner.threshold * 1.15)
    return tuner.threshold


def rank_score(item):
    # 來源分數 + 動量權重
    return item.get("_score", 0.0)


async def try_trade(item):
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

        raw_score = await alpha(m)

        regime.update(raw_score)
        engine.regime = regime.mode

        score = raw_score * regime.multiplier()
        thr = dynamic_threshold(score)

        log(f"SCORE {m[:6]} score={score:.4f} thr={thr:.4f} regime={engine.regime}")

        # v4：高分直接快速通道
        fast_lane = score >= SETTINGS["FAST_ENTRY_THRESHOLD"]

        if score < thr and not fast_lane:
            engine.stats["rejected"] += 1
            log(f"REJECT {m[:6]} score={score:.4f} thr={thr:.4f}")
            return

        # 高分單給更大 size，普通單保守
        lamports = 300000 if fast_lane else 200000

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

        if impact > SETTINGS["LIQUIDITY_IMPACT_MAX"] and not fast_lane:
            engine.stats["rejected"] += 1
            log(f"HIGH_IMPACT {m[:8]} impact={impact:.4f}")
            return

        log(
            f"QUOTE_OK {m[:8]} "
            f"out={out_amount} "
            f"impact={impact:.4f} "
            f"fast={fast_lane}"
        )

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
            f"out={out_amount} "
            f"fast={fast_lane}"
        )

        LAST_TRADE[m] = now

        pnl = score - 0.01
        tuner.update(pnl)
        engine.threshold = tuner.threshold
        engine.capital *= (1 + pnl)

        engine.trade_history.append({
            "token": m,
            "score": score,
            "raw_score": raw_score,
            "pnl": pnl,
            "regime": engine.regime,
            "impact": impact,
            "outAmount": out_amount,
            "fast": fast_lane,
            "mode": "paper_v4_dynamic",
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

        scored = []
        for item in items:
            mint = item.get("mint")
            if not mint:
                continue

            # 先做輕量 ranking：讓同輪只打前幾名
            base = item.get("momentum", 0) if isinstance(item, dict) else 0
            scored.append({**item, "_score": base})

        ranked = sorted(scored, key=rank_score, reverse=True)[:TOP_N]

        for item in ranked:
            await try_trade(item)

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

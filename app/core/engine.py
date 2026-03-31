import asyncio

from app.alpha.alpha import alpha
from app.data.market import get_quote
from app.execution.jupiter import order
from app.regime.regime import regime
from app.ai.tuner import tuner
from app.risk.liquidity import liquidity_ok
from app.risk.anti_rug import anti_rug
from app.wallet.manager import wallet_scale, load_wallets
from app.core.state import engine
from app.sources.pump import fetch_pump_candidates

SOL = "So11111111111111111111111111111111111111112"


def log(msg: str):
    msg = str(msg)
    print(msg)
    engine.logs.append(msg)
    engine.logs = engine.logs[-500:]


async def process(e, source="pump"):
    try:
        m = e.get("mint")
        if not m:
            return

        log(f"PROCESSING: {e}")
        engine.stats["signals"] += 1

        # ===== 流動性 =====
        if not await liquidity_ok(m):
            engine.stats["rejected"] += 1
            log(f"REJECT_LIQ {m[:6]}")
            return

        # ===== anti-rug =====
        if not await anti_rug(m):
            engine.stats["rejected"] += 1
            log(f"REJECT_RUG {m[:6]}")
            return

        # ===== alpha =====
        score = await alpha(m)

        regime.update(score)
        engine.regime = regime.mode
        score *= regime.multiplier()

        if score < tuner.threshold:
            engine.stats["rejected"] += 1
            log(f"REJECT {m[:6]} score={score:.4f} thr={tuner.threshold:.4f}")
            return

        # ===== 多錢包 =====
        for wallet_name, weight in wallet_scale().items():
            # Debug 先縮很小，避免餘額問題
            size = 0.00001 * regime.multiplier() * weight
            lamports = int(size * 1e9)

            log(
                f"TRY_ORDER {m[:8]} wallet={wallet_name} "
                f"size={size:.8f} lamports={lamports} score={score:.4f}"
            )

            # ===== 先抓 quote =====
            q = await get_quote(SOL, m, lamports)
            if not q:
                log(f"QUOTE_FAIL {m[:8]} wallet={wallet_name}")
                continue

            log(
                f"QUOTE_OK {m[:8]} wallet={wallet_name} "
                f"out={q.get('outAmount')} impact={q.get('priceImpactPct')}"
            )

            # ===== 用 quote 建 order =====
            o = await order(SOL, m, lamports, quote=q)
            if not o or not o.get("transaction"):
                log(f"ORDER_FAIL {m[:8]} wallet={wallet_name} data={o}")
                continue

            # ===== Debug：先不 execute，先確認流程通到這 =====
            log(
                f"PAPER_EXEC {m[:8]} wallet={wallet_name} "
                f"score={score:.4f} regime={engine.regime}"
            )

            # ===== PnL（debug 先保留簡化）=====
            pnl = score - 0.01
            tuner.update(pnl)
            engine.threshold = tuner.threshold
            engine.capital *= (1 + pnl)

            engine.trade_history.append({
                "token": m,
                "wallet": wallet_name,
                "score": score,
                "pnl": pnl,
                "regime": engine.regime,
                "source": source,
                "mode": "paper_debug",
            })

            engine.stats["executed"] += 1

    except Exception as ex:
        engine.stats["errors"] += 1
        log(f"PROCESS_ERR {ex}")


async def safe_cycle():
    print("scanning...")

    try:
        items = await fetch_pump_candidates()
        print("PUMP ITEMS:", items)

        for item in items:
            print("PROCESSING ITEM:", item)
            await process(item, source="pump")

    except Exception as e:
        log(f"SAFE_CYCLE_ERR {e}")

    await asyncio.sleep(5)


async def main_loop():
    log("ENGINE LOOP START")
    load_wallets()

    while True:
        try:
            await safe_cycle()
        except Exception as e:
            engine.stats["errors"] += 1
            log(f"LOOP ERROR: {e}")
            await asyncio.sleep(2)

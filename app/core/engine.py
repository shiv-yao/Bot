import asyncio

from app.alpha.alpha import alpha
from app.execution.jupiter import order, safe_jupiter_call
from app.execution.jito import send_bundle
from app.regime.regime import regime
from app.ai.tuner import tuner
from app.risk.liquidity import liquidity_ok
from app.risk.anti_rug import anti_rug
from app.wallet.manager import wallet_scale, load_wallets
from app.core.state import engine
from app.sources.pump import fetch_pump_candidates

SOL = "So11111111111111111111111111111111111111112"


# ================= LOG =================
def log(msg: str):
    msg = str(msg)
    print(msg)
    engine.logs.append(msg)
    engine.logs = engine.logs[-500:]


# ================= PROCESS =================
async def process(e, source="pump"):
    try:
        m = e.get("mint")
        if not m:
            return

        engine.stats["signals"] += 1

        # ===== 風控 =====
        if not await liquidity_ok(m):
            engine.stats["rejected"] += 1
            log(f"REJECT_LIQ {m[:6]}")
            return

        if not await anti_rug(m):
            engine.stats["rejected"] += 1
            log(f"REJECT_RUG {m[:6]}")
            return

        # ===== alpha =====
        score = await alpha(m)

        regime.update(score)
        engine.regime = regime.mode
        score *= regime.multiplier()

        # 🔥 關鍵 log（你剛剛問的就在這）
        if score < tuner.threshold:
            engine.stats["rejected"] += 1
            log(f"REJECT {m[:6]} score={score:.4f} thr={tuner.threshold:.4f}")
            return

        # ===== 多錢包 =====
        for wallet_name, weight in wallet_scale().items():
            size = 0.002 * regime.multiplier() * weight
            lamports = int(size * 1e9)

            # ===== Jupiter order =====
            o = await order(SOL, m, lamports)
            if not o or not o.get("transaction"):
                log(f"ORDER_FAIL {m[:8]} wallet={wallet_name}")
                continue

            # ===== 安全執行 =====
            res = await safe_jupiter_call(o)
            if not res:
                log(f"EXEC_FAIL {m[:8]} wallet={wallet_name}")
                continue

            # ===== Jito =====
            try:
                if await send_bundle(o):
                    engine.stats["jito_sent"] += 1
            except Exception as e:
                log(f"JITO_ERR {e}")

            # ===== PnL =====
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
            })

            engine.stats["executed"] += 1

            log(
                f"EXEC {m[:8]} wallet={wallet_name} "
                f"pnl={pnl:.4f} regime={engine.regime}"
            )

    except Exception as ex:
        engine.stats["errors"] += 1
        log(f"PROCESS_ERR {ex}")


# ================= SAFE CYCLE =================
async def safe_cycle():
    print("scanning...")

    try:
        items = await fetch_pump_candidates()
        print("PUMP ITEMS:", items)

        for item in items:
            print("PROCESSING:", item)
            await process(item, source="pump")

    except Exception as e:
        log(f"SAFE_CYCLE_ERR {e}")

    await asyncio.sleep(5)


# ================= MAIN LOOP =================
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

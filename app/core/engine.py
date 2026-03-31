import asyncio

from app.mempool.decode import stream
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


def log(msg: str):
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-500:]
    print(msg)


async def process(e, source="mempool"):
    try:
        m = e.get("mint")
        if not m:
            return

        if source == "mempool":
            engine.stats["mempool_seen"] += 1

        engine.stats["signals"] += 1

        if not await liquidity_ok(m):
            engine.stats["rejected"] += 1
            return

        if not await anti_rug(m):
            engine.stats["rejected"] += 1
            return

        score = await alpha(m)

        regime.update(score)
        engine.regime = regime.mode
        score *= regime.multiplier()

        if score < tuner.threshold:
            engine.stats["rejected"] += 1
            return

        for wallet_name, weight in wallet_scale().items():
            size = 0.002 * regime.multiplier() * weight
            lamports = int(size * 1e9)

            o = await order(SOL, m, lamports)
            if not o or not o.get("transaction"):
                log(f"ORDER_FAIL {m[:8]} wallet={wallet_name}")
                continue

            res = await safe_jupiter_call(o)
            if not res:
                log(f"EXEC_FAIL {m[:8]} wallet={wallet_name}")
                continue

            try:
                if await send_bundle(o):
                    engine.stats["jito_sent"] += 1
            except Exception as jito_err:
                log(f"JITO_ERR {jito_err}")

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
                f"pnl={pnl:.4f} regime={engine.regime} source={source}"
            )

    except Exception as ex:
        engine.stats["errors"] += 1
        log(f"PROCESS_ERR {ex}")


async def pump_loop():
    while True:
        try:
            items = await fetch_pump_candidates()
            for item in items:
                await process(item, source="pump")
        except Exception as ex:
            engine.stats["errors"] += 1
            log(f"PUMP_ERR {ex}")

        await asyncio.sleep(20)


async def mempool_loop():
    async def handler(e):
        await process(e, source="mempool")

    while True:
        try:
            await stream(handler)
        except Exception as ex:
            engine.stats["errors"] += 1
            log(f"MEMPOOL_ERR {ex}")
            await asyncio.sleep(3)


async def safe_cycle():
    log("ENGINE_HEARTBEAT")
    await asyncio.sleep(10)


async def main_loop():
    log("ENGINE LOOP START")
    load_wallets()

    asyncio.create_task(pump_loop())
    asyncio.create_task(mempool_loop())

    while True:
        try:
            await safe_cycle()
        except Exception as e:
            engine.stats["errors"] += 1
            log(f"LOOP ERROR: {e}")
            await asyncio.sleep(2)

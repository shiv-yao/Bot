import asyncio
from app.execution.quote import get_quote
from app.execution.jupiter import order
from app.core.state import engine

SOL = "So11111111111111111111111111111111111111112"


def log(msg):
    print(msg)
    engine.logs.append(msg)
    engine.logs = engine.logs[-500:]


async def process(item):
    try:
        m = item.get("mint")
        if not m:
            return

        log(f"PROCESSING: {item}")

        lamports = 200000  # 小額測試

        # 🔥 取得 quote
        quote = await get_quote(SOL, m, lamports)

        if not quote:
            log(f"NO_QUOTE {m[:8]}")
            return

        # 🔥 呼叫 Jupiter swap
        o = await order(SOL, m, lamports, quote=quote)

        if not o or not o.get("transaction"):
            log(f"ORDER_FAIL {m[:8]} data={o}")
            return

        log(f"PAPER_EXEC {m[:8]} SUCCESS")

        engine.stats["executed"] += 1

    except Exception as e:
        log(f"PROCESS_ERR {e}")


# ================= LOOP =================

async def safe_cycle():
    print("scanning...")

    try:
        from app.sources.pump import fetch_pump_candidates

        items = await fetch_pump_candidates()

        print("PUMP ITEMS:", items)

        for item in items:
            await process(item)

    except Exception as e:
        log(f"SAFE_CYCLE_ERR {e}")

    await asyncio.sleep(5)


async def main_loop():
    print("ENGINE LOOP START")

    while True:
        try:
            await safe_cycle()
        except Exception as e:
            print("LOOP ERROR:", e)
            await asyncio.sleep(2)

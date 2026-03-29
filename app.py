# ================= v1314 FINAL APP (NO FEATURE REMOVED) =================
import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from state import engine

BOT_TASK = None


# ================= INIT =================

def init_engine():
    """
    🔥 不動你原本邏輯，只補齊避免 crash
    """
    engine.running = True
    engine.mode = getattr(engine, "mode", "PAPER")

    if not hasattr(engine, "positions"):
        engine.positions = []

    if not hasattr(engine, "logs"):
        engine.logs = []

    if not hasattr(engine, "trade_history"):
        engine.trade_history = []

    if not hasattr(engine, "stats"):
        engine.stats = {
            "signals": 0,
            "buys": 0,
            "sells": 0,
            "errors": 0,
            "adds": 0,
        }

    if not hasattr(engine, "engine_stats"):
        engine.engine_stats = {
            "stable": {"pnl": 0.0, "trades": 0, "wins": 0},
            "degen": {"pnl": 0.0, "trades": 0, "wins": 0},
            "sniper": {"pnl": 0.0, "trades": 0, "wins": 0},
        }

    if not hasattr(engine, "engine_allocator"):
        engine.engine_allocator = {
            "stable": 0.33,
            "degen": 0.33,
            "sniper": 0.34,
        }

    if not hasattr(engine, "capital"):
        engine.capital = 30.0

    if not hasattr(engine, "sol_balance"):
        engine.sol_balance = 30.0

    if not hasattr(engine, "candidate_count"):
        engine.candidate_count = 0

    if not hasattr(engine, "last_trade"):
        engine.last_trade = ""

    if not hasattr(engine, "last_signal"):
        engine.last_signal = ""

    if not hasattr(engine, "bot_ok"):
        engine.bot_ok = True

    if not hasattr(engine, "bot_error"):
        engine.bot_error = ""


# ================= BOT =================

async def start_bot():
    global BOT_TASK

    # 🔥 防止重複啟動
    if BOT_TASK and not BOT_TASK.done():
        return

    try:
        from bot import bot_loop

        BOT_TASK = asyncio.create_task(bot_loop())

        engine.bot_ok = True
        engine.bot_error = ""

        engine.logs.append("BOT_STARTED")

    except Exception as e:
        engine.bot_ok = False
        engine.bot_error = str(e)

        # 🔥 保底 log（避免 logs 不是 list）
        try:
            engine.logs.append(f"BOT_ERROR {e}")
        except:
            pass


async def monitor_bot():
    """
    🔥 核心升級：Bot 掛掉自動重啟
    """
    global BOT_TASK

    while True:
        try:
            if BOT_TASK is None or BOT_TASK.done():
                engine.logs.append("BOT_RESTART")
                await start_bot()

        except Exception as e:
            try:
                engine.logs.append(f"MONITOR_ERR {e}")
            except:
                pass

        await asyncio.sleep(5)


# ================= LIFESPAN =================

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_engine()

    await start_bot()

    # 🔥 背景守護（關鍵）
    asyncio.create_task(monitor_bot())

    yield


app = FastAPI(lifespan=lifespan)


# ================= API =================

@app.get("/health")
def health():
    return {
        "ok": True,
        "bot_ok": getattr(engine, "bot_ok", False),
        "bot_error": getattr(engine, "bot_error", ""),
        "running": getattr(engine, "running", True),
        "mode": getattr(engine, "mode", "UNKNOWN"),
    }


@app.get("/data")
def data():
    """
    🔥 UI 不再崩潰（核心修復）
    """
    try:
        snapshot = engine.snapshot()

        # ===== 防 None 崩潰 =====
        snapshot["positions"] = snapshot.get("positions") or []
        snapshot["logs"] = snapshot.get("logs") or []
        snapshot["trade_history"] = snapshot.get("trade_history") or []
        snapshot["stats"] = snapshot.get("stats") or {}
        snapshot["engine_stats"] = snapshot.get("engine_stats") or {}
        snapshot["engine_allocator"] = snapshot.get("engine_allocator") or {}

        return snapshot

    except Exception as e:
        return JSONResponse({
            "error": str(e),
            "safe": True
        })


# ================= DEBUG API =================

@app.get("/restart")
async def restart():
    """
    🔥 手動重啟 bot
    """
    global BOT_TASK

    try:
        if BOT_TASK:
            BOT_TASK.cancel()
    except:
        pass

    await start_bot()

    return {"status": "restarted"}


@app.get("/kill")
async def kill():
    """
    🔥 緊急停機
    """
    global BOT_TASK

    try:
        if BOT_TASK:
            BOT_TASK.cancel()
    except:
        pass

    engine.running = False
    return {"status": "stopped"}


# ================= RUN =================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        log_level="info"
    )

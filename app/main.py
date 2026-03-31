import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI

# ========= GLOBAL =========
BOT_TASK = None


# ========= LIFESPAN =========
@asynccontextmanager
async def lifespan(app: FastAPI):
    global BOT_TASK

    print("🚀 APP STARTING...")

    try:
        from app.core.engine import main_loop

        BOT_TASK = asyncio.create_task(main_loop())
        print("✅ ENGINE STARTED")

    except Exception as e:
        print("❌ ENGINE IMPORT ERROR:", repr(e))

    yield

    print("🛑 SHUTTING DOWN...")

    if BOT_TASK:
        BOT_TASK.cancel()
        try:
            await BOT_TASK
        except:
            pass

    print("✅ CLEAN EXIT")


# ========= APP =========
app = FastAPI(lifespan=lifespan)


# ========= ROOT =========
@app.get("/")
def root():
    return {
        "status": "running",
        "msg": "bot alive"
    }


# ========= DEBUG =========
@app.get("/debug")
def debug():
    try:
        from app.core.state import engine

        return {
            "running": engine.running,
            "positions": engine.positions,
            "stats": engine.stats,
            "last_signal": getattr(engine, "last_signal", ""),
            "logs": engine.logs[-20:] if hasattr(engine, "logs") else []
        }

    except Exception as e:
        return {
            "error": str(e)
        }

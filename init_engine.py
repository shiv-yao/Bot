import os
from state import engine
from wallet import load_keypair


def init_engine():
    engine.running = True

    if not hasattr(engine, "positions") or not isinstance(engine.positions, list):
        engine.positions = []

    if not hasattr(engine, "logs") or not isinstance(engine.logs, list):
        engine.logs = []

    if not hasattr(engine, "trade_history") or not isinstance(engine.trade_history, list):
        engine.trade_history = []

    if not hasattr(engine, "stats") or not isinstance(engine.stats, dict):
        engine.stats = {
            "signals": 0,
            "buys": 0,
            "sells": 0,
            "errors": 0,
            "adds": 0
        }

    if not hasattr(engine, "engine_stats") or not isinstance(engine.engine_stats, dict):
        engine.engine_stats = {
            "stable": {"pnl": 0.0, "trades": 0, "wins": 0},
            "degen": {"pnl": 0.0, "trades": 0, "wins": 0},
            "sniper": {"pnl": 0.0, "trades": 0, "wins": 0},
        }

    if not hasattr(engine, "engine_allocator") or not isinstance(engine.engine_allocator, dict):
        engine.engine_allocator = {
            "stable": 0.4,
            "degen": 0.4,
            "sniper": 0.2,
        }

    if not hasattr(engine, "candidate_count"):
        engine.candidate_count = 0

    if not hasattr(engine, "capital"):
        engine.capital = 30.0

    if not hasattr(engine, "last_signal"):
        engine.last_signal = ""

    if not hasattr(engine, "last_trade"):
        engine.last_trade = ""

    engine.wallet_ok = False
    engine.jup_ok = False
    engine.bot_ok = False
    engine.bot_error = ""

    requested_real = os.environ.get("REAL_TRADING", "false").lower() == "true"

    try:
        engine.wallet = load_keypair()
        engine.wallet_ok = True
        engine.logs.append("✅ wallet loaded")
    except Exception as e:
        engine.wallet_ok = False
        engine.bot_error = str(e)
        engine.logs.append(f"❌ wallet error: {e}")

    jup_key = os.environ.get("JUP_API_KEY", "").strip()
    engine.jup_ok = bool(jup_key)

    if engine.jup_ok:
        engine.logs.append("✅ jupiter ready")
    else:
        engine.logs.append("❌ jupiter missing")

    if requested_real and engine.wallet_ok and engine.jup_ok:
        engine.mode = "REAL"
        engine.bot_ok = True
        engine.logs.append("🔥 REAL TRADING ENABLED")
    else:
        engine.mode = "PAPER"
        engine.bot_ok = False
        engine.logs.append("⚠️ FALLBACK TO PAPER")

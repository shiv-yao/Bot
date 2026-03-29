import os
from state import engine
from wallet import load_keypair


def init_engine():
    engine.logs.append("🚀 INIT ENGINE")

    # ===== MODE =====
    engine.requested_mode = (
        "REAL" if os.getenv("REAL_TRADING", "false").lower() == "true" else "PAPER"
    )

    # ===== WALLET =====
    try:
        engine.wallet = load_keypair()
        engine.wallet_ok = True
        engine.logs.append("✅ wallet loaded")
    except Exception as e:
        engine.wallet_ok = False
        engine.bot_error = str(e)
        engine.logs.append(f"❌ wallet error: {e}")

    # ===== JUP =====
    jup_key = os.getenv("JUP_API_KEY", "").strip()
    engine.jup_ok = bool(jup_key)

    if engine.jup_ok:
        engine.logs.append("✅ jupiter ready")
    else:
        engine.logs.append("❌ jupiter missing")

    # ===== FINAL MODE =====
    if engine.requested_mode == "REAL" and engine.wallet_ok and engine.jup_ok:
        engine.mode = "REAL"
        engine.bot_ok = True
        engine.logs.append("🔥 REAL TRADING ENABLED")
    else:
        engine.mode = "PAPER"
        engine.bot_ok = False
        engine.logs.append("⚠️ FALLBACK TO PAPER")

    return engine

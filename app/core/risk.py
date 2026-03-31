# app/core/risk.py

MAX_POSITIONS = 3
MAX_ORDER = 0.02
MAX_DAILY_LOSS = 0.2

def allow_trade(state):
    if len(state.positions) >= MAX_POSITIONS:
        return False, "MAX_POS"

    if state.capital <= 0:
        return False, "NO_CAP"

    return True, ""

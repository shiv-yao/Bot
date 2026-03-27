import statistics

LAST_PNLS = []

def update_regime(pnl):
    LAST_PNLS.append(pnl)
    if len(LAST_PNLS) > 30:
        LAST_PNLS.pop(0)

def get_regime():
    if len(LAST_PNLS) < 10:
        return "neutral"

    avg = statistics.mean(LAST_PNLS)

    if avg > 0.02:
        return "bull"

    if avg < -0.01:
        return "bear"

    return "neutral"

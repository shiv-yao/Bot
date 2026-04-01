MAX_POSITIONS = 3
MAX_EXPOSURE = 0.25
MIN_CAPITAL = 0.50


def total_exposure(engine):
    return sum(p["size"] for p in engine.positions)


def dynamic_risk_factor(engine):
    total = engine.stats["wins"] + engine.stats["losses"]

    if total < 5:
        return 1.0

    winrate = engine.stats["wins"] / total

    if winrate > 0.65:
        return 1.2
    elif winrate < 0.45:
        return 0.7

    return 1.0


def allow(engine, score, size):
    # 🚀 測試期：先全部放行
    return True

    if total_exposure(engine) + size > MAX_EXPOSURE:
        engine.log("MAX_EXPOSURE")
        return False

    if engine.capital < size:
        engine.log("NO_CAPITAL")
        return False

    if score < 0.45:
        engine.log("LOW_SCORE")
        return False

    return True


def kill_switch(engine):
    if engine.capital < MIN_CAPITAL:
        engine.running = False
        engine.log("KILL_SWITCH")
        return True

    return False

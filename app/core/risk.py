MAX_POSITIONS = 3
MAX_EXPOSURE = 0.25   # 🔥 總倉位上限
MIN_CAPITAL = 0.50

def total_exposure(engine):
    return sum(p["size"] for p in engine.positions)


def dynamic_risk_factor(engine):
    """
    根據勝率 + 回撤動態調整風險
    """
    total = engine.stats["wins"] + engine.stats["losses"]
    if total < 5:
        return 1.0

    winrate = engine.stats["wins"] / total

    # 勝率好 → 放大
    if winrate > 0.65:
        return 1.2
    elif winrate < 0.45:
        return 0.7

    return 1.0


def allow(engine, score, size):
    # 🔒 倉位數限制
    if len(engine.positions) >= MAX_POSITIONS:
        engine.log("MAX_POS")
        return False

    # 🔒 總曝險限制
    if total_exposure(engine) > MAX_EXPOSURE:
        engine.log("MAX_EXPOSURE")
        return False

    # 🔒 資金限制
    if engine.capital < size:
        engine.log("NO_CAPITAL")
        return False

    # 🔒 分數門檻
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

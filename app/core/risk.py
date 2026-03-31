MAX_POSITIONS = 3
MIN_CAPITAL = 0.50
MAX_DRAWDOWN = 0.20


def allow(engine, score, size):
    if len(engine.positions) >= MAX_POSITIONS:
        engine.log("MAX_POS")
        return False

    if engine.capital < size:
        engine.log("NO_CAPITAL")
        return False

    if score < 0.45:
        engine.log("RISK_REJECT_LOW_SCORE")
        return False

    return True


def kill_switch(engine):
    if engine.capital < MIN_CAPITAL:
        engine.running = False
        engine.log("KILL_SWITCH_MIN_CAPITAL")
        return True

    if engine.peak_capital > 0:
        dd = (engine.capital - engine.peak_capital) / engine.peak_capital
        if dd <= -MAX_DRAWDOWN:
            engine.running = False
            engine.log("KILL_SWITCH_MAX_DRAWDOWN")
            return True

    return False

MAX_POSITIONS = 3
MAX_DAILY_LOSS = 0.20
MIN_CAPITAL = 0.50


def allow(engine):
    if len(engine.positions) >= MAX_POSITIONS:
        engine.log("MAX_POS")
        return False
    return True


def kill_switch(engine):
    # 資金跌破底線就停機
    if engine.capital < MIN_CAPITAL:
        engine.running = False
        engine.log("KILL_SWITCH")
        return True
    return False

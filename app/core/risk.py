MAX_POS = 3


def allow(engine):
    if len(engine.positions) >= MAX_POS:
        engine.log("MAX_POS")
        return False
    return True

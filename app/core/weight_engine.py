weights = {
    "b": 0.2,
    "s": 0.2,
    "l": 0.1,
    "i": 0.1,
    "w": 0.2,
    "c": 0.1,
    "i2": 0.1,
}

def adjust_weights(pnl):
    global weights

    if pnl > 0:
        weights["w"] += 0.01
        weights["s"] += 0.01
    else:
        weights["b"] += 0.01

    total = sum(weights.values())
    for k in weights:
        weights[k] /= total

def get_weights():
    return weights

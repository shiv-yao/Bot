WEIGHTS = {
    "momentum": 1,
    "volume": 1,
    "flow": 1
}

def compute(features):
    return sum(WEIGHTS[k]*v for k,v in features.items())

def learn(features, pnl):
    for k,v in features.items():
        WEIGHTS[k] += 0.01 * pnl * v

def position_size(score, capital):
    base = capital * 0.02

    if score > 0.6:
        base *= 1.5

    if score > 0.8:
        base *= 2

    return base

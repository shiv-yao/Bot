def dynamic_size(score):
    if score > 0.03:
        return 0.02
    elif score > 0.02:
        return 0.01
    return 0.005

from allocator import allocator

def record_trade(strategy, pnl):
    allocator.update(strategy, pnl)

def get_weight(strategy):
    weights = allocator.compute_weights()
    return weights.get(strategy, 0.05)

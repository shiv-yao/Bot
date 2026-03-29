from allocator import allocator


def record_trade(strategy: str, pnl: float):
    allocator.update(strategy, pnl)


def get_weight(strategy: str) -> float:
    return allocator.weight(strategy)

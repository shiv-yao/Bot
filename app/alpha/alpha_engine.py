import random


def base_alpha(volume, change):
    """
    基礎 alpha：
    - change: 近 1h 價格變動百分比
    - volume: 24h 交易量
    """
    mom = change / 100.0
    vol = min(volume / 1_000_000, 1.0)
    noise = random.random() * 0.003

    return mom * 0.6 + vol * 0.3 + noise


def compute_alpha(volume, change, flow):
    """
    融合版 alpha：
    - base_alpha 佔 60%
    - smart money / flow 佔 40%
    """
    return base_alpha(volume, change) * 0.6 + flow * 0.4

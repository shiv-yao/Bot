def liquidity_score(token: dict) -> float:
    """
    流動性品質分數
    現在 scanner 至少有 volume / change，可先用 volume 當代理
    """
    volume = float(token.get("volume", 0))
    change = float(token.get("change", 0))

    base = min(volume / 200000.0, 1.0)

    # 漲跌幅過大視為不穩定，扣分
    if abs(change) > 20:
        base *= 0.5

    return base

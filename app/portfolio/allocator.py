def get_position_size(score: float, capital: float, regime: str) -> float:
    """
    分級倉位
    """

    if regime == "flat":
        risk_mult = 0.6
    elif regime == "trend_down":
        risk_mult = 0.5
    elif regime == "volatile":
        risk_mult = 0.8
    else:
        risk_mult = 1.0

    if score >= 0.72:
        raw = 0.12
    elif score >= 0.62:
        raw = 0.08
    else:
        raw = 0.05

    sized = raw * risk_mult

    # 不超過現有資金的 20%
    sized = min(sized, capital * 0.20)

    # 至少給一點空間
    sized = max(min(sized, capital), 0.02)

    return round(sized, 4)

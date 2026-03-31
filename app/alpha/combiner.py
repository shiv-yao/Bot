def combine_scores(
    breakout: float,
    smart_money: float,
    liquidity: float,
    regime: str,
) -> float:
    """
    三策略融合：
    breakout 主導
    smart money 次之
    liquidity 作底
    """

    score = (
        breakout * 0.50
        + smart_money * 0.30
        + liquidity * 0.20
    )

    # regime 調整
    if regime == "trend_up":
        score *= 1.08
    elif regime == "flat":
        score *= 0.82
    elif regime == "trend_down":
        score *= 0.72
    elif regime == "volatile":
        score *= 0.92

    return min(score, 1.0)

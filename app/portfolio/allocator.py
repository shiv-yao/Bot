from app.core.risk import dynamic_risk_factor


def get_position_size(score: float, capital: float, engine) -> float:
    """
    🔥 動態倉位（關鍵升級）
    """

    risk_adj = dynamic_risk_factor(engine)

    # 🎯 分數 → 倉位
    if score >= 0.72:
        base = 0.08
    elif score >= 0.62:
        base = 0.05
    else:
        base = 0.03

    size = base * risk_adj

    # 🔒 不超過資金 20%
    size = min(size, capital * 0.20)

    # 🔒 至少 0.02
    size = max(size, 0.02)

    return round(size, 4)

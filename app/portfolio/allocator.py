from app.core.risk import dynamic_risk_factor


def get_position_size(score: float, capital: float, engine) -> float:
    risk_adj = dynamic_risk_factor(engine)

    if score >= 0.72:
        base = capital * 0.08
    elif score >= 0.62:
        base = capital * 0.05
    else:
        base = capital * 0.03

    size = base * risk_adj
    size = min(size, capital * 0.20)
    size = max(size, 0.02)

    return round(size, 4)

REGIME = "neutral"


def regime_risk_multiplier(regime: str) -> float:
    if regime == "bull":
        return 1.15
    if regime == "chop":
        return 0.75
    if regime == "trash":
        return 0.0
    return 1.0


def regime_take_profit(regime: str, default_tp: float) -> float:
    if regime == "bull":
        return max(default_tp, 0.15)
    if regime == "chop":
        return min(default_tp, 0.08)
    if regime == "trash":
        return 0.0
    return default_tp


def regime_stop_loss(regime: str, default_sl: float) -> float:
    if regime == "bull":
        return default_sl
    if regime == "chop":
        return min(default_sl, 0.03)
    if regime == "trash":
        return min(default_sl, 0.02)
    return default_sl

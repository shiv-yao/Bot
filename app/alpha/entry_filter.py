def momentum_confirm(momentum: float) -> bool:
    return momentum >= 0.004


def fake_pump_filter(change: float, volume: float) -> bool:
    """
    過濾假拉盤：
    - 漲很多但量不夠
    """
    if change > 12 and volume < 80000:
        return False
    return True


def liquidity_trap_filter(volume: float) -> bool:
    """
    避免低流動性陷阱
    """
    return volume > 30000


def smart_money_confirm(smart_score: float) -> bool:
    return smart_score >= 0.35


def score_alpha(b: float, s: float, l: float) -> float:
    return b * 0.5 + s * 0.3 + l * 0.2


def classify_alpha(score: float) -> str:
    if score >= 0.7:
        return "A+"
    elif score >= 0.6:
        return "A"
    elif score >= 0.5:
        return "B"
    else:
        return "C"

def should_enter(token: str, features: dict):
    momentum = features.get("momentum", 0)
    smart_money = features.get("smart_money", 0)

    # 🔥 允許 smart_money = 0（初期必須）
    if momentum < 0:
        return False, "bad_momentum"

    # 🔥 改成加分條件，不是硬門檻
    if smart_money <= 0:
        return True, "weak_smart_money"

    return True, "ok"
